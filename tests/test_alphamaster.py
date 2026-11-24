import copy
import json

import pytest
from test_pipeline import project as project

from alpha_atlas.checkpoint import Checkpoint, read_json
from alpha_atlas.methods.models import HTTPModels
from alpha_atlas.runner import resume, run
from alpha_atlas.storage import RunStore


def test_online_dual_chain_recovers_saved_response_and_counts_every_trial(project, monkeypatch):
    calls = []

    def chat(self, system, payload):
        calls.append(copy.deepcopy(payload))
        assert "remaining_attempts" not in payload["context"]
        expression = "$adj_close" if len(calls) == 1 else "$missing"
        return json.dumps({"expression": expression, "hypothesis": "synthetic"}), None

    monkeypatch.setattr(HTTPModels, "chat", chat)
    original = Checkpoint.save
    with monkeypatch.context() as patch:

        def stop_after_response(self, method, *args, **kwargs):
            original(self, method, *args, **kwargs)
            if method.responses:
                raise KeyboardInterrupt

        patch.setattr(Checkpoint, "save", stop_after_response)
        with pytest.raises(KeyboardInterrupt):
            run(project, "ashare", "fold1", "alphamaster", 42, 13)
    directory = next((project / "artifacts/runs").iterdir())
    resume(project, directory)
    trials = list(RunStore(directory).trials())
    assert len(calls) == len(trials) == 13
    assert read_json(directory / "run.json")["status"] == "frozen"
    assert any(c["references"]["discarded"] for c in calls)
    assert any(c["optimize"] is not None for c in calls)
    for i, payload in enumerate(calls):
        if payload["optimize"] is not None:
            assert payload["optimize"]["trial"] == i
            assert not trials[i - 1]["feedback"]["accepted"]
            assert calls[i - 1]["optimize"] is None


def test_batch_keeps_ranked_references_and_optimizes_at_most_once():
    from types import SimpleNamespace

    from alpha_atlas.contracts import TrialFeedback
    from alpha_atlas.methods import make_method

    members = [
        {"candidate": {"expression": f"$f{i}"}, "primary_val_ic": score}
        for i, score in enumerate([0.02, 0.03, 0.03, 0.01])
    ]

    class Session:
        def query(self, name, args):
            if name == "library_list":
                return {"members": members.copy(), "total": len(members)}
            assert name == "get_context"
            return {"fields": ["$x"]}

    class Chat:
        def chat(self, system, payload):
            calls.append(copy.deepcopy(payload))
            return '{"expression":"$x","hypothesis":"synthetic"}', None

    method = make_method("alphamaster", 42, {"chat_model": "synthetic"})
    method.set_session(Session())
    method.models = Chat()
    calls = []
    for i in range(11):
        candidate = method.ask(SimpleNamespace(remaining_attempts=11 - i))[0]
        val = SimpleNamespace(split="val", value=i / 100)
        report = SimpleNamespace(metrics=[val], coverage=1.0)
        method.tell(
            [TrialFeedback(candidate, report, False, "quality_threshold", factor_trial_index=i + 1)]
        )
        if i == 0:
            members.append({"candidate": {"expression": "$late"}, "primary_val_ic": 0.9})
    best = calls[0]["references"]["effective"]
    assert [row["expression"] for row in best] == ["$f1", "$f2", "$f0"]
    assert all(call["references"] == calls[0]["references"] for call in calls[:10])
    assert calls[10]["references"]["effective"][0]["expression"] == "$late"
    assert [row["trial"] for row in calls[10]["references"]["discarded"]] == [2, 4, 6]
    assert [call["optimize"]["trial"] for call in calls if call["optimize"]] == [1, 3, 5, 7, 9]
