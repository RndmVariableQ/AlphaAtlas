"""V1 research stages with bounded concurrent model I/O and one state-writing thread."""

from __future__ import annotations

import copy
import json
import math
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass

from alpha_atlas.methods.annealing import AnnealingLLMSearch
from alpha_atlas.methods.models import _RetryableError
from alpha_atlas.methods.v1 import (
    CONTENT_FIELDS,
    ExplorerDialogueLimitError,
    ExplorerUnresolved,
    V1Config,
    V1Search,
    _normalize,
    _unresolved,
)
from alpha_atlas.storage import candidate_from_dict
from alpha_atlas.terminal import terminal_progress


@dataclass(frozen=True)
class V1AsyncConfig(V1Config):
    parallel_plans: int = 3
    # Candidates of different plans computed at once; admission stays serial in trial order.
    parallel_evaluations: int = 1
    # Explorer dialogues that may fill free plan slots at once; each opens at most four plans.
    parallel_explorers: int = 1

    def __post_init__(self):
        super().__post_init__()
        if type(self.parallel_plans) is not int or self.parallel_plans < 1:
            raise ValueError("parallel_plans must be a positive integer")
        if (
            type(self.parallel_evaluations) is not int
            or not 1 <= self.parallel_evaluations <= self.parallel_plans
        ):
            # Each plan waits for its own feedback, so it has at most one candidate in flight.
            raise ValueError("parallel_evaluations must be between 1 and parallel_plans")
        if (
            type(self.parallel_explorers) is not int
            or not 1 <= self.parallel_explorers <= self.parallel_plans
        ):
            raise ValueError("parallel_explorers must be between 1 and parallel_plans")


class _AwaitingModel(Exception):
    """Yield the saved research stage until its model request can be consumed."""


class _ConcurrentModels:
    """Resume the existing checkpointed stages without running their state in workers."""

    streaming = False

    def __init__(self, method):
        self.method = method

    def chat(self, system, payload, *, temperature=None):
        if "Atlas Explorer Agent" in system:
            system += (
                "\navailable_slots is the number of plans the server can start now; it starts at "
                "most that many of the submitted directions and drops the rest.\n"
            )
            payload = {
                **payload,
                "active_proposals": self.method.research_plan_state["active_proposals"],
                "available_slots": len(self.method._active[1]["quotas"]),
            }
        return self.chat_messages(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=temperature,
        )

    def chat_messages(self, messages, *, on_delta=None, temperature=None):
        # The inherited Miner may pass a terminal stream; workers must never touch it.
        return self.method._request(messages, temperature)


class V1AsyncSearch(V1Search):
    """Independent plans; each Miner waits only for its own platform feedback."""

    def __init__(self, seed, config=None, *, models=None):
        super().__init__(seed, config or V1AsyncConfig(), models=models)
        self._transport = self.models
        self.models = _ConcurrentModels(self)
        self.groups = {}
        # Running Explorer dialogues by request key; "explorer" is the first (and, serially, only).
        self.explorers = {}
        self.next_research_plan_index = 1
        self.pending_group = None
        # Plans whose candidate is being evaluated, in trial order (parallel_evaluations > 1).
        self.evaluating = []
        self._active = None
        self._executor = None
        self._requests = {}

    @contextmanager
    def _activate(self, key, group):
        # research_plan_state/work are temporary views onto ONE group's authoritative state.
        self._active = (key, group)
        self.research_plan_state, self.work = group["research_plan_state"], group["work"]
        try:
            yield
        finally:
            self._sync_active()
            self._active = None
            self.research_plan_state = self.work = None

    def _sync_active(self):
        if self._active is not None:
            group = self._active[1]
            group["research_plan_state"], group["work"] = self.research_plan_state, self.work

    def dump_state(self):
        self._sync_active()
        state = super().dump_state()
        del state["research_plan_state"], state["work"]
        return {
            **state,
            "version": "atlas_explorer_v1_async",
            "groups": {
                key: group for key, group in self.groups.items() if group["research_plan_state"]
            },
            **(
                {"explorers": self.explorers}
                if self.explorer_slots > 1
                else {"explorer": self.explorers.get("explorer")}
            ),
            "next_research_plan_index": self.next_research_plan_index,
            "pending_group": self.pending_group if self.pending is not None else None,
            **({"evaluating": self.evaluating} if self.evaluation_slots > 1 else {}),
        }

    @property
    def configuration(self):
        config = super().configuration
        for name in ("parallel_evaluations", "parallel_explorers"):
            if config.get(name) == 1:
                config.pop(name)  # Serial runs keep their recorded configuration.
        return config

    @property
    def explorer(self):
        # The serial checkpoint field; with parallel_explorers > 1 the state is "explorers".
        return self.explorers.get("explorer")

    @explorer.setter
    def explorer(self, value):
        self.explorers = {} if value is None else {"explorer": value}

    @property
    def explorer_slots(self):
        return getattr(self.config, "parallel_explorers", 1)

    @property
    def evaluation_slots(self):
        """How many candidates the runner may compute at once."""
        return getattr(self.config, "parallel_evaluations", 1)

    def load_state(self, state):
        if (
            state.get("version") != "atlas_explorer_v1_async"
            or state.get("config") != self.configuration
        ):
            raise ValueError("v1_async checkpoint or configuration changed")
        self.close()
        rng = state["rng"]
        self.rng.setstate((rng[0], tuple(rng[1]), rng[2]))
        self._load_research_fields(state)

    def _start_request(self, kind):
        # Resuming a saved stage is not a new request. Count only actual submissions below.
        pass

    @property
    def pending_research_plan_index(self):
        # With several candidates in flight, the next feedback belongs to the oldest one.
        key = self.evaluating[0] if self.evaluating else self.pending_group
        group = self.groups.get(key)
        return group["research_plan_state"]["research_plan_index"] if group is not None else None

    def _request_progress(self, title, **values):
        # Reentering the saved stage to consume a future is not another model request.
        if self._active[0] not in self._requests:
            super()._request_progress(title, **values)

    def _request(self, messages, temperature):
        key = self._active[0]
        job = self._requests.get(key)
        if job is not None and job["future"] is not None:
            if not job["future"].done():
                raise _AwaitingModel
            try:
                result = job["future"].result()
            except _RetryableError as exc:
                if job["attempts"] == 4:
                    raise RuntimeError(f"{exc}; failed after 4 requests") from None
                delay = exc.retry_delay(job["attempts"] - 1)
                job["future"] = None
                job["retry_at"] = time.monotonic() + delay
                terminal_progress("异步模型重试", 组=key, 等待秒=delay, 原因=str(exc))
            else:
                del self._requests[key]
                return result
        if job is None:
            job = {
                "messages": copy.deepcopy(messages),
                "temperature": temperature,
                "attempts": 0,
                "future": None,
                "retry_at": 0,
            }
            self._requests[key] = job
        if time.monotonic() >= job["retry_at"]:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.config.parallel_plans + 1, thread_name_prefix="v1-model"
                )
            AnnealingLLMSearch._start_request(self, "chat")
            job["attempts"] += 1
            job["future"] = self._executor.submit(
                self._transport.chat_once,
                job["messages"],
                temperature=job["temperature"],
            )
        raise _AwaitingModel

    def _runnable(self, key):
        job = self._requests.get(key)
        return (
            job is None
            or (job["future"] is not None and job["future"].done())
            or (job["future"] is None and time.monotonic() >= job["retry_at"])
        )

    def _analyst_previous_round(self):
        if "analyst_feedback" not in self.research_plan_state:
            last = next(
                (
                    record["research_plan_index"]
                    for record in reversed(self.factor_trial_records)
                    if "explorer_proposals" in record or "annealing_result" in record
                ),
                0,
            )
            self.research_plan_state["analyst_feedback"] = super()._analyst_previous_round(last)
        return self.research_plan_state["analyst_feedback"]

    def _catalog(self):
        snapshot, catalog = super()._catalog()
        # A new region can already have an Analyst before runner receives its first candidate.
        return {**snapshot, "regions": list(catalog.values())}, catalog

    def _validate_proposals(self, raw, catalog, available_refs):
        batch = super()._validate_proposals(raw, catalog, available_refs)
        active = self.research_plan_state["active_proposals"]
        if self.explorer_slots > 1:
            # Another Explorer may have opened plans since this dialogue's snapshot was taken.
            active = active + [
                group["research_plan_state"]["exploration"]["proposal"]
                for group in self.groups.values()
            ]
        if any(
            all(_normalize(item[k]) == _normalize(other[k]) for k in CONTENT_FIELDS[1:])
            for item in batch["proposals"]
            for other in active
        ):
            raise ValueError("proposal duplicates an active research direction")
        return batch

    def _explorer_waiting(self, explorer=None):
        explorer = self.explorer if explorer is None else explorer
        records = explorer["research_plan_state"]["responses"]["explorer"]
        return _unresolved(records, self.config.max_dialogue_rounds) or bool(
            len(records) >= self.config.max_dialogue_rounds
            and records[-1].get("processed")
            and ("error" in records[-1] or "query" in records[-1])
        )

    def _retire_explorer(self):
        if not self.explorers or self.groups:
            return
        for key, explorer in list(self.explorers.items()):
            state = explorer["research_plan_state"]
            records = state["responses"]["explorer"]
            if not state["active_proposals"] or not self._explorer_waiting(explorer):
                continue
            # Keep the bounded dialogue in the existing research history, then use new evidence.
            history = self.factor_trial_records or self.blocked_plans
            history[-1]["deferred_explorer"] = {
                "research_plan_index": state["research_plan_index"],
                "responses": records,
            }
            del self.explorers[key]

    def _start_explorer(self, context):
        self._retire_explorer()
        while len(self.explorers) < self.explorer_slots:
            # Running Explorers have already claimed their slots and attempt quotas.
            claimed = [q for explorer in self.explorers.values() for q in explorer["quotas"]]
            reserved = sum(
                group["research_plan_state"]["candidate_limit"]
                - len(group["research_plan_state"]["outcomes"])
                for group in self.groups.values()
            ) + sum(claimed)
            available = context.remaining_attempts - reserved
            slots = min(4, self.config.parallel_plans - len(self.groups) - len(claimed), available)
            if slots <= 0:
                return
            quotas = []
            for remaining_slots in range(slots, 0, -1):
                quota = min(self.config.candidates_per_plan, math.ceil(available / remaining_slots))
                quotas.append(quota)
                available -= quota
            state = self._new_round(context, self.next_research_plan_index)
            self.next_research_plan_index += 1
            state["active_proposals"] = [
                group["research_plan_state"]["exploration"]["proposal"]
                for group in self.groups.values()
            ]
            key = next(
                k
                for k in ("explorer", *(f"explorer-{n}" for n in range(2, self.explorer_slots + 1)))
                if k not in self.explorers
            )
            self.explorers[key] = {"research_plan_state": state, "work": None, "quotas": quotas}
            self._checkpoint()

    def _advance_explorer(self, context):
        for key in list(self.explorers):
            self._advance_one_explorer(context, key)

    def _advance_one_explorer(self, context, key):
        explorer = self.explorers[key]
        if not self._runnable(key):
            return
        with self._activate(key, explorer):
            snapshot, catalog = self._catalog()
            state = self.research_plan_state
            was_waiting = self._explorer_waiting(explorer)
            if was_waiting and state["active_proposals"]:
                return
            try:
                super()._explore_round(snapshot, catalog, select=False)
            except _AwaitingModel:
                return
            except (ExplorerUnresolved, ExplorerDialogueLimitError) as exc:
                if not state["active_proposals"]:
                    raise
                if not was_waiting:
                    terminal_progress(
                        "Explorer 等待活动计划",
                        计划=state["research_plan_index"],
                        活动组=len(self.groups),
                        原因=str(exc),
                    )
                return
            batch = state["explorer_proposals"]
            order = self._select_proposals(
                batch["proposals"], len(explorer["quotas"]), state["temperature"]
            )
            # The validated batch and group creation are committed together, before any Analyst.
            for offset, (selected, quota) in enumerate(
                zip(order, explorer["quotas"], strict=False)
            ):
                if offset == 0:
                    group_state = state
                else:
                    group_state = self._new_round(context, self.next_research_plan_index)
                    self.next_research_plan_index += 1
                proposal = batch["proposals"][selected]
                group_state["exploration"] = self._proposal_exploration(
                    proposal, catalog, set(proposal["references"]), batch["sources"]
                )
                self.region_scores.setdefault(
                    group_state["exploration"]["region_id"], {"accepted": 0, "attempts": 0}
                )
                catalog.update(self.proposed_regions)
                for field in ("temperature", "budget_progress", "mode"):
                    group_state[field] = state[field]
                group_state["explorer_proposals"] = {**batch, "selected": selected}
                group_state["candidate_limit"] = quota
                self.groups[str(group_state["research_plan_index"])] = {
                    "research_plan_state": group_state,
                    "work": None,
                }
                terminal_progress(
                    f"[计划 #{group_state['research_plan_index']} · Explorer] 方向已提交",
                    方向=group_state["exploration"]["direction"],
                )
        del self.explorers[key]
        self._checkpoint()

    def ask(self, context, count=1, *, block=True):
        """Up to count ready candidates of distinct plans; with block=False, [] when none is ready.

        Serial runs (parallel_evaluations = 1) hand out one candidate and expect its feedback first.
        """
        serial = self.evaluation_slots == 1
        if count != 1 if serial else not 1 <= count <= self.evaluation_slots:
            raise ValueError("ask count exceeds parallel_evaluations")
        # A checkpoint can precede runner's pending-trial checkpoint. Return its saved candidate.
        if serial and self.pending is not None:
            return [candidate_from_dict(self.pending)]
        if context.remaining_attempts <= 0:
            raise ValueError("attempt budget exhausted")
        if self.context is None:
            self.context = self._session.query("get_context", {})
        while True:
            self._check_blocked_limit()
            self._start_explorer(context)
            self._advance_explorer(context)
            for key, group in list(self.groups.items()):
                if "candidate" in (group["work"] or {}) or not self._runnable(key):
                    continue
                with self._activate(key, group):
                    try:
                        self._prepare_candidate(context)
                    except _AwaitingModel:
                        pass
                if group["research_plan_state"] is None:
                    del self.groups[key]
                    self._retire_explorer()
            ready = [
                key
                for key, group in self.groups.items()
                if key not in self.evaluating and "candidate" in (group["work"] or {})
            ]
            if ready and serial:
                candidate = self.groups[ready[0]]["work"]["candidate"]
                self.pending, self.pending_group = candidate, ready[0]
                self._checkpoint()
                return [candidate_from_dict(candidate)]
            if ready:
                # The runner checkpoints these together with its in-flight trials.
                self.evaluating += ready[:count]
                return [
                    candidate_from_dict(self.groups[key]["work"]["candidate"])
                    for key in ready[:count]
                ]
            if not block:
                return []
            # Model completions wake this thread; it alone queries, validates and saves state.
            futures = [job["future"] for job in self._requests.values() if job["future"]]
            deadlines = [
                job["retry_at"] for job in self._requests.values() if job["future"] is None
            ]
            timeout = max(0, min([1.0, *(deadline - time.monotonic() for deadline in deadlines)]))
            if futures:
                wait(futures, timeout=timeout, return_when=FIRST_COMPLETED)
            elif deadlines:
                time.sleep(timeout)

    def tell(self, results):
        if self.evaluation_slots > 1:
            # Feedback arrives in trial order, which is the order candidates were handed out.
            if len(results) > len(self.evaluating):
                raise ValueError("v1_async received feedback without a candidate in flight")
            for feedback in results:
                key = self.evaluating.pop(0)
                group = self.groups[key]
                with self._activate(key, group):
                    self.pending = group["work"]["candidate"]
                    super().tell([feedback])
                if group["research_plan_state"] is None:
                    del self.groups[key]
            self._retire_explorer()
            self._checkpoint()
            return
        if self.pending_group not in self.groups:
            raise ValueError("v1_async expects one pending feedback")
        key = self.pending_group
        group = self.groups[key]
        with self._activate(key, group):
            super().tell(results)
        if group["research_plan_state"] is None:
            del self.groups[key]
        self.pending_group = None
        self._retire_explorer()
        self._checkpoint()

    def close(self):
        if self._executor is not None:
            # Running HTTP calls cannot be cancelled; they have a timeout and cannot mutate state.
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        self._requests.clear()
