"use strict";
// AlphaAtlas dashboard: read-only views of run artifacts. All facts come from /api/*.

const $ = (s) => document.querySelector(s);
const state = {
  runs: [], runId: null, summary: null, plans: null, map: null,
  time: 0, live: true, playing: false, tab: "map",
  view: { k: 1, x: 0, y: 0 }, hover: null, selected: null, completed: -1,
};
const STATUS_TEXT = { running: "运行中", frozen: "已冻结", failed: "失败", interrupted: "已中断", stalled: "疑似停滞" };
const MAP_STATUS = { land: "陆地", saturated: "饱和", seabed: "海底", probing: "勘探中" };
const REASON_TEXT = {
  accepted: "入库", quality_threshold: "质量未达标", behavior_duplicate: "行为重复",
  expression_duplicate: "表达式重复", low_coverage: "覆盖不足", compile_error: "编译失败",
  constant_factor: "常量因子", compute_error: "计算失败", budget_rejected: "预算耗尽",
  undefined_train_metric: "Train 指标未定义", undefined_val_metric: "Val 指标未定义",
};

const api = async (path) => {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
};
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 4) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
const signed = (v, d = 4) => (v === null || v === undefined ? "—" : `<span class="${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}</span>`);
const ago = (t, now) => {
  if (!t) return "—";
  const s = Math.max(0, (now ?? Date.now() / 1000) - t);
  if (s < 60) return `${Math.round(s)} 秒前`;
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} 小时前`;
  return `${(s / 86400).toFixed(1)} 天前`;
};
const liveness = (run) => {
  if (run.status !== "running") return run.status || "unknown";
  const idle = Date.now() / 1000 - (run.last_activity || 0);
  return idle > 1800 ? "stalled" : "running";
};

// ---------------------------------------------------------------- run list
async function loadRuns() {
  try { state.runs = await api("/api/runs"); } catch { return; }
  renderRuns();
  if (!state.runId && state.runs.length) {
    const wanted = new URLSearchParams(location.hash.slice(1)).get("run");
    const preferred = state.runs.find((r) => r.id === wanted) || state.runs.find((r) => r.residual) || state.runs[0];
    selectRun(preferred.id);
  }
}

function renderRuns() {
  const filter = $("#run-filter").value.trim().toLowerCase();
  $("#run-list").innerHTML = state.runs
    .filter((r) => !filter || JSON.stringify(r).toLowerCase().includes(filter))
    .map((r) => {
      const live = liveness(r);
      const pct = r.attempts ? Math.min(100, (100 * r.completed) / r.attempts) : 0;
      return `<div class="run ${r.id === state.runId ? "active" : ""}" data-id="${esc(r.id)}">
        <div class="row1"><span class="dot ${live}"></span><span class="name" title="${esc(r.method)} · ${esc(r.model || "无模型")}">${esc(r.method)} · ${esc(r.model || "无模型")}</span></div>
        <div class="row2">${esc(r.fold || "")} · ${r.completed}/${r.attempts ?? "?"} · ${esc(STATUS_TEXT[live] || live)} · ${ago(r.last_activity)}</div>
        <div class="row2">${esc(r.version || r.asset || "")}</div>
        <div class="bar"><i style="width:${pct}%"></i></div></div>`;
    })
    .join("");
}

$("#run-list").addEventListener("click", (e) => {
  const item = e.target.closest(".run");
  if (item) selectRun(item.dataset.id);
});
$("#run-filter").addEventListener("input", renderRuns);

function writeHash() {
  history.replaceState(null, "", `#run=${encodeURIComponent(state.runId || "")}&tab=${state.tab}`);
}

async function selectRun(id) {
  state.runId = id;
  writeHash();
  state.completed = -1;
  state.map = null;
  state.plans = null;
  state.selected = null;
  state.view = { k: 1, x: 0, y: 0 };
  state.live = true;
  $("#live").checked = true;
  closeDetail();
  renderRuns();
  await refresh(true);
}

// ---------------------------------------------------------------- polling
async function refresh(force = false) {
  if (!state.runId) return;
  let summary;
  try { summary = await api(`/api/summary?id=${encodeURIComponent(state.runId)}`); } catch { return; }
  state.summary = summary;
  renderHeader();
  const changed = summary.completed !== state.completed;
  if (changed || force) {
    state.completed = summary.completed;
    const [plans, map] = await Promise.all([
      api(`/api/plans?id=${encodeURIComponent(state.runId)}`).catch(() => null),
      api(`/api/map?id=${encodeURIComponent(state.runId)}`).catch(() => null),
    ]);
    state.plans = plans;
    state.map = map;
    const last = map?.available ? map.last_trial : summary.completed;
    $("#time").max = last;
    if (state.live || force) state.time = last;
    const linkedTime = force && state.linkedDetail?.get("t");
    if (linkedTime) {
      state.time = clamp(Number(linkedTime), 0, last);
      state.live = false;
      $("#live").checked = false;
    }
    $("#time").value = state.time;
    renderPlans();
    renderMonitor();
    drawSpark();
    scheduleMap();
    if (force && state.linkedDetail) {
      const link = state.linkedDetail;
      state.linkedDetail = null;
      // Optional zoom: z=<factor>, centred on at=<x>,<y> (layout units) or the linked Region.
      if (link.get("z") && map?.available) {
        let [ax, ay] = link.get("at") ? link.get("at").split(",").map(Number) : [NaN, NaN];
        if (!Number.isFinite(ay)) ax = NaN;
        const members = map.points.filter((p) => p.region === link.get("region"));
        if (!Number.isFinite(ax) && members.length) {
          ax = members.reduce((s, p) => s + p.x, 0) / members.length;
          ay = members.reduce((s, p) => s + p.y, 0) / members.length;
        }
        if (!Number.isFinite(ax)) [ax, ay] = [0.5, 0.5];
        const k = clamp(Number(link.get("z")), 0.5, 12);
        const S = Math.min(canvas.width, canvas.height);
        const ox = (canvas.width - S) / 2, oy = (canvas.height - S) / 2;
        state.view = {
          k,
          x: canvas.width / 2 - ox - (ax * S - S / 2) * k - S / 2,
          y: canvas.height / 2 - oy - (ay * S - S / 2) * k - S / 2,
        };
        scheduleMap();
      }
      if (link.get("plan")) showPlan(Number(link.get("plan")));
      else if (link.get("trial")) showTrial(Number(link.get("trial")));
      else if (link.get("region")) showRegion(link.get("region"));
    }
  } else {
    renderMonitor();
  }
}

function renderHeader() {
  const s = state.summary;
  const live = liveness({ status: s.status, last_activity: s.last_activity });
  $("#run-title").textContent = `${s.method} · ${s.model || "无模型"}`;
  const pill = $("#run-status");
  pill.className = `pill ${live}`;
  pill.textContent = STATUS_TEXT[live] || live;
  const stop = $("#run-stop");
  stop.hidden = s.status !== "running";
  if (stop.dataset.run !== state.runId) {
    stop.dataset.run = state.runId;
    stop.disabled = false;
    stop.textContent = "停止运行";
  }
  $("#run-meta").innerHTML = [s.asset, s.fold, s.version, s.run_id].filter(Boolean).map((v) => `<span>${esc(v)}</span>`).join("");
  const info = s.curve.length ? s.curve[s.curve.length - 1].information : 0;
  $("#run-stats").innerHTML = [
    ["完成 / 预算", `${s.completed} / ${s.attempts ?? "?"}`],
    ["新增入库", s.admitted],
    ["库规模", s.library_size],
    ["入库因子残差", s.residual ? fmt(info, 3) : "未开启"],
    ["最后活动", ago(s.last_activity, s.now)],
    ...(s.stop_reason ? [["提前结束", s.stop_reason]] : []),
  ].map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${k}</span></div>`).join("");
  $("#run-progress").style.width = `${s.attempts ? Math.min(100, (100 * s.completed) / s.attempts) : 0}%`;
}

$("#run-stop").addEventListener("click", async () => {
  const button = $("#run-stop");
  const id = state.runId;
  if (!id || !confirm(`停止运行 ${id}？\n先发 Ctrl+C（SIGINT），约 20 秒未退出再发 SIGTERM；运行记为“已中断”，已提交的 trial 保留，之后可以 resume 继续。`)) return;
  button.disabled = true;
  button.textContent = "正在停止…（最多约 40 秒）";
  try {
    const response = await fetch(`/api/stop?id=${encodeURIComponent(id)}`, {
      method: "POST", headers: { "X-Atlas-Action": "stop" },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || response.status);
    button.textContent = `已停止（${body.signals.join(" → ")}）`;
  } catch (error) {
    alert(`停止失败：${error.message}`);
    button.disabled = false;
    button.textContent = "停止运行";
  }
});

// ---------------------------------------------------------------- tabs
function switchTab(tab) {
  const button = document.querySelector(`#tabs button[data-tab="${tab}"]`);
  if (button) button.click();
}
document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    state.tab = b.dataset.tab;
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${state.tab}`));
    if (state.tab === "map") scheduleMap();
    if (state.tab === "monitor") renderMonitor();
    if (state.runId) writeHash();
  })
);

// ---------------------------------------------------------------- terrain
const canvas = $("#map");
const ctx = canvas.getContext("2d");
let frame = null;

function hash(x, y) {
  let h = (x * 374761393 + y * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}
function valueNoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const a = hash(xi, yi), b = hash(xi + 1, yi), c = hash(xi, yi + 1), d = hash(xi + 1, yi + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
function fbm(x, y, octaves = 5) {
  let total = 0, amp = 0.5, freq = 1;
  for (let i = 0; i < octaves; i++) {
    total += amp * (valueNoise(x * freq, y * freq) * 2 - 1);
    freq *= 2.03; amp *= 0.5;
  }
  return total;
}
const mix = (a, b, t) => a + (b - a) * t;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
function ramp(stops, t) {
  t = clamp(t, 0, 1);
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1], [t1, c1] = stops[i];
      const f = (t - t0) / (t1 - t0 || 1);
      return [mix(c0[0], c1[0], f), mix(c0[1], c1[1], f), mix(c0[2], c1[2], f)];
    }
  }
  return stops[stops.length - 1][1];
}
// One palette for the whole map, after true-colour satellite imagery.
const LAND = [
  [0, [198, 184, 144]], [0.012, [156, 152, 106]], [0.045, [96, 118, 64]], [0.25, [78, 100, 54]],
  [0.5, [112, 106, 72]], [0.75, [136, 122, 96]], [1, [166, 160, 150]],
];
const ROCK = [132, 124, 114];
const DRY = [176, 152, 104];
const AMBIENT = [8, 14, 26]; // Blue sky light fills shadows.
const SEABED = [196, 184, 146]; // Lit sand on explored ground.
const OOZE = [70, 78, 84]; // Unexplored sea floor.
const DEEP = [9, 30, 58];
const ABSORB = [1.05, 0.5, 0.32]; // Per-depth-unit attenuation for red, green and blue.
const HILL = 1.2; // Width of a factor's contribution to the land field, in layout sigmas.

function visiblePoints() {
  if (!state.map?.available) return [];
  return state.map.points.filter((p) => p.trial <= state.time);
}

function regionStatus(regionId) {
  const region = state.map?.regions.find((r) => r.id === regionId);
  if (!region) return null;
  let current = null;
  for (const step of region.timeline) if (step.trial <= state.time) current = step;
  return current;
}

function elevation(p) {
  if (p.kind === "initial") return 1.2;
  if (p.residual === null || p.residual === undefined) return null;
  const sea = state.map.sea_level;
  return clamp((p.residual - sea) / sea, -3, 4);
}

function toScreen(x, y) {
  const S = Math.min(canvas.width, canvas.height);
  const ox = (canvas.width - S) / 2, oy = (canvas.height - S) / 2;
  return [ox + (x * S - S / 2) * state.view.k + S / 2 + state.view.x, oy + (y * S - S / 2) * state.view.k + S / 2 + state.view.y];
}
function toWorld(sx, sy) {
  const S = Math.min(canvas.width, canvas.height);
  const ox = (canvas.width - S) / 2, oy = (canvas.height - S) / 2;
  return [((sx - ox - S / 2 - state.view.x) / state.view.k + S / 2) / S, ((sy - oy - S / 2 - state.view.y) / state.view.k + S / 2) / S];
}

function resize() {
  const r = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(r.width * dpr));
  canvas.height = Math.max(1, Math.round(r.height * dpr));
  scheduleMap();
}
window.addEventListener("resize", resize);

function scheduleMap() {
  if (state.tab !== "map") return;
  if (frame) return;
  frame = requestAnimationFrame(() => { frame = null; drawMap(); });
}

// Terrain is rendered in world coordinates into an offscreen canvas and only redrawn with a
// transform while panning or zooming. Noise fields do not depend on data, so they are cached
// per world rectangle and resolution; the data pass is a few array sweeps.
const noiseCache = { key: null };
const baseLayer = { key: null, canvas: document.createElement("canvas"), rect: null };
const detailLayer = { key: null, canvas: document.createElement("canvas"), rect: null };
let detailTimer = null;
const BASE_RECT = { x0: -0.1, y0: -0.1, x1: 1.1, y1: 1.1 };
const OCEAN = "rgb(10, 40, 62)";

// Ridged multifractal: sharp crests where the noise crosses zero, smoother valleys, and finer
// octaves weighted by the coarser crest so peaks carry detail and lowlands stay calm.
function ridged(x, y, octaves = 5) {
  let total = 0, amp = 0.55, f = 1, weight = 1;
  for (let o = 0; o < octaves; o++) {
    let r = 1 - Math.abs(valueNoise(x * f, y * f) * 2 - 1);
    r *= r * weight;
    weight = clamp(r * 1.8, 0, 1);
    total += r * amp;
    f *= 2.07; amp *= 0.5;
  }
  return clamp(total / 0.95, 0, 1);
}

function noiseFields(rect, gw, gh, freq) {
  const key = `${rect.x0},${rect.y0},${rect.x1},${rect.y1}|${gw}x${gh}|${freq}`;
  if (noiseCache.key === key) return noiseCache;
  const n = gw * gh;
  const f = {
    key, shiftX: new Float32Array(n), shiftY: new Float32Array(n), warpN: new Float32Array(n),
    coast: new Float32Array(n), ridge: new Float32Array(n), ragged: new Float32Array(n),
    fine: new Float32Array(n), grain: new Float32Array(n), swell: new Float32Array(n),
    surf: new Float32Array(n), mount: new Float32Array(n),
  };
  for (let y = 0; y < gh; y++) {
    for (let x = 0; x < gw; x++) {
      const i = y * gw + x;
      const wx = rect.x0 + ((x + 0.5) / gw) * (rect.x1 - rect.x0);
      const wy = rect.y0 + ((y + 0.5) / gh) * (rect.y1 - rect.y0);
      f.shiftX[i] = fbm(wx * freq + 31.7, wy * freq - 12.9, 4);
      f.shiftY[i] = fbm(wx * freq - 7.1, wy * freq + 45.3, 4);
      const warp = fbm(wx * 6 + 5.2, wy * 6 - 1.3, 4);
      f.warpN[i] = fbm(wx * 11 + 2 * warp, wy * 11 - 2 * warp);
      f.coast[i] = fbm(wx * 3.4 + 9.1, wy * 3.4 - 4.7, 4);
      f.ridge[i] = 1 - Math.abs(fbm(wx * 8 + 1.7 * warp, wy * 8 - 1.7 * warp, 5));
      f.ragged[i] = 0.55 * f.warpN[i] + 0.35 * fbm(wx * 27 - 3.3 * warp, wy * 27 + 3.3 * warp, 3);
      f.fine[i] = fbm(wx * 34, wy * 34, 3);
      f.grain[i] = fbm(wx * 90 + 3, wy * 90, 3);
      f.swell[i] = fbm(wx * 2.2 + 17, wy * 2.2 - 5, 3);
      f.surf[i] = valueNoise(wx * 300, wy * 300);
      // Mountain crests sized to a few factor spacings, so each island gets its own peaks.
      f.mount[i] = ridged(wx * freq * 2.3 + 2.9 * f.warpN[i], wy * freq * 2.3 - 2.9 * f.warpN[i]);
    }
  }
  Object.assign(noiseCache, f);
  return noiseCache;
}

function renderTerrain(points, rect, gw, gh, target) {
  target.width = gw; target.height = gh;
  const tc = target.getContext("2d");
  const n = gw * gh;
  const spacing = state.map.spacing || 0.032;
  const ppw = gw / (rect.x1 - rect.x0); // grid cells per world unit
  const fields = noiseFields(rect, gw, gh, 1 / (3.2 * spacing));
  const density = new Float32Array(n), heightSum = new Float32Array(n), dry = new Float32Array(n);
  // Land field: kernel-weighted coverage and elevation of factors above sea level. Summing
  // (not taking a maximum of per-factor domes) keeps neighbouring factors one seamless massif.
  const landW = new Float32Array(n), landE = new Float32Array(n), hills = new Float32Array(n);
  const coreLand = new Uint8Array(n);
  // Smoothing follows the layout's point spacing so Regions stay separate islands.
  const sigmaPx = Math.max(1.2, 0.8 * spacing * ppw);
  const reach = Math.ceil(sigmaPx * 4);
  // Domain warp (below one sigma) gives irregular outlines; each factor's core stays land.
  const warpPx = 0.75 * sigmaPx;
  const inv = 1 / (2 * sigmaPx * sigmaPx), invHill = 1 / (2 * HILL * HILL * sigmaPx * sigmaPx);
  const invKnoll = 1 / (2 * 0.6 * 0.6 * sigmaPx * sigmaPx);
  const status = {};
  for (const p of points) {
    const e = elevation(p);
    if (e === null) continue;
    if (p.region && !(p.region in status)) status[p.region] = regionStatus(p.region)?.status;
    const gx = (p.x - rect.x0) * ppw, gy = (p.y - rect.y0) * ppw;
    if (gx < -reach || gy < -reach || gx > gw + reach || gy > gh + reach) continue;
    const saturated = status[p.region] === "saturated" ? 1 : 0;
    const x0 = Math.max(0, Math.floor(gx - reach)), x1 = Math.min(gw - 1, Math.ceil(gx + reach));
    const y0 = Math.max(0, Math.floor(gy - reach)), y1 = Math.min(gh - 1, Math.ceil(gy + reach));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const i = y * gw + x;
        const dx = x + warpPx * fields.shiftX[i] - gx, dy = y + warpPx * fields.shiftY[i] - gy;
        const d2 = dx * dx + dy * dy;
        const w = Math.exp(-d2 * inv);
        density[i] += w; heightSum[i] += w * e; dry[i] += w * saturated;
        if (e > 0) {
          const g = Math.exp(-d2 * invHill);
          landW[i] += g; landE[i] += g * e;
          hills[i] += e * Math.exp(-d2 * invKnoll); // a knoll at each factor, summed: no seams
          if ((x - gx) ** 2 + (y - gy) ** 2 < 0.25 * sigmaPx * sigmaPx) coreLand[i] = 1;
        }
      }
    }
  }
  const H = new Float32Array(n), explored = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const nz = fields.warpN[i];
    // Low-frequency noise only scales measured density: no explored area without attempts.
    const raw = 1 - Math.exp(-density[i] / 0.55);
    const m = smooth(0.16, 0.66, raw * (1 + 0.95 * fields.coast[i] + 0.35 * nz));
    explored[i] = m;
    const mean = density[i] > 1e-4 ? heightSum[i] / density[i] : -3;
    // Where factors above sea level cover the cell, their weighted elevation replaces the
    // all-attempt mean, so one strong factor is not averaged away by ordinary neighbours.
    // Land rises toward where above-sea factors overlap most and slopes down to a gentle
    // coast, so an island is a dome or a range rather than a flat plateau.
    const cover = smooth(0.12, 1.1, landW[i]);
    const dome = landW[i] > 1e-6
      ? (landE[i] / landW[i]) * (0.4 + 0.6 * smooth(0.25, 3.5, landW[i])) + 0.35 * Math.log1p(landW[i])
      : 0;
    const base = cover > 0 ? mix(mean, dome, cover) : mean;
    // Ragged shore inside a narrow band only; a factor's core is always land.
    const band = 1 - smooth(0, 0.6, Math.abs(base));
    let shore = base + 0.38 * fields.ragged[i] * band;
    if (coreLand[i]) shore = Math.max(shore, 0.02);
    // Relief rises toward dense cores with ridged spurs, fading to zero at the coast so
    // texture never changes the sign of the measured elevation.
    const core = 1 - Math.exp(-density[i] / 2.2);
    const texture = clamp(
      0.95 * (core - 0.55) + (shore > 0 ? 0.8 : 0.14) * (fields.ridge[i] - 0.55) + 0.06 * fields.fine[i],
      -1, 1,
    );
    // Land: the dome carries ridged mountains and a knoll per factor. Every term is positive
    // and scales with the dome, so relief vanishes at the coast and never moves it.
    const h = shore > 0
      ? shore * (0.45 + 1.5 * fields.mount[i]) + 0.55 * hills[i] * cover
        + 0.12 * shore * fields.fine[i]
      : shore + Math.min(0.85 * Math.abs(shore), 0.9) * texture;
    H[i] = m * h + (1 - m) * (-3.4 + 0.5 * nz);
  }
  const image = tc.createImageData(gw, gh);
  const data = image.data;
  // Satellite-style shading: one north-west light and one colour model for all terrain.
  // Relief exaggeration scales with resolution so the map reads the same at any zoom.
  const light = [-0.5, -0.6, 0.62];
  const lightLen = Math.hypot(...light);
  const exaggerate = 3.2 * ppw / 340;
  for (let y = 0; y < gh; y++) {
    for (let x = 0; x < gw; x++) {
      const i = y * gw + x;
      const h = H[i], m = explored[i];
      const hx = H[y * gw + Math.min(gw - 1, x + 1)] - H[y * gw + Math.max(0, x - 1)];
      const hy = H[Math.min(gh - 1, y + 1) * gw + x] - H[Math.max(0, y - 1) * gw + x];
      const nx = -hx * exaggerate, ny = -hy * exaggerate;
      const len = Math.hypot(nx, ny, 1);
      const shade = clamp((nx * light[0] + ny * light[1] + light[2]) / (len * lightLen), 0, 1);
      const slope = clamp(Math.hypot(nx, ny) / 2.5, 0, 1);
      let r, g, b;
      if (h >= 0) {
        // Land: beach, vegetated lowland, brown upland and bare rock by height and slope.
        let c = ramp(LAND, h / 3);
        const dryness = density[i] > 1e-4 ? dry[i] / density[i] : 0;
        const dryMix = 0.7 * dryness * smooth(0.05, 0.3, h);
        const rockMix = 0.55 * slope * smooth(0.2, 1, h);
        const relief = 0.38 + 0.82 * shade;
        const tint = 1 + 0.06 * fields.grain[i];
        [r, g, b] = c.map((v, k) => {
          const dried = mix(v, DRY[k], dryMix);
          return (mix(dried, ROCK[k], rockMix) * relief + (1 - shade) * AMBIENT[k]) * tint;
        });
      } else {
        // Water: the floor (lit sand on explored ground, dark sediment elsewhere) attenuated
        // per channel with depth, red first, blending into deep-ocean blue.
        const depth = -h;
        const floorLit = 0.45 + 0.75 * shade;
        const swell = 1 + 0.035 * fields.swell[i];
        const surf = h > -0.12 && m > 0.15 ? (1 + h / 0.12) * 0.5 * (0.7 + 0.3 * fields.surf[i]) : 0;
        [r, g, b] = SEABED.map((v, k) => {
          const t = Math.exp(-ABSORB[k] * depth);
          const water = (mix(OOZE[k], v, m) * floorLit * t + DEEP[k] * (1 - t)) * swell;
          return mix(water, 238, surf);
        });
      }
      const o = i * 4;
      data[o] = r; data[o + 1] = g; data[o + 2] = b; data[o + 3] = 255;
    }
  }
  tc.putImageData(image, 0, 0);
}

function terrainKey() {
  return `${state.runId}|${state.time}|${state.map.last_trial}|${state.map.spacing}`;
}

function visibleRect() {
  const [x0, y0] = toWorld(0, 0);
  const [x1, y1] = toWorld(canvas.width, canvas.height);
  return { x0, y0, x1, y1 };
}

function drawLayer(layer) {
  const [sx0, sy0] = toScreen(layer.rect.x0, layer.rect.y0);
  const [sx1, sy1] = toScreen(layer.rect.x1, layer.rect.y1);
  ctx.drawImage(layer.canvas, sx0, sy0, sx1 - sx0, sy1 - sy0);
}

function scheduleDetail() {
  // A sharp re-render of the visible area once zooming or panning pauses.
  clearTimeout(detailTimer);
  if (state.view.k < 1.4) return;
  detailTimer = setTimeout(() => {
    const rect = visibleRect();
    const key = `${terrainKey()}|${rect.x0.toFixed(4)},${rect.y0.toFixed(4)},${rect.x1.toFixed(4)}`;
    if (detailLayer.key === key) return;
    const gw = Math.min(800, Math.ceil(canvas.width / 2));
    const gh = Math.max(1, Math.round(gw * (rect.y1 - rect.y0) / (rect.x1 - rect.x0)));
    renderTerrain(visiblePoints(), rect, gw, gh, detailLayer.canvas);
    detailLayer.key = key; detailLayer.rect = rect; detailLayer.base = terrainKey();
    scheduleMap();
  }, 180);
}

function drawTerrain(points) {
  const key = terrainKey();
  if (baseLayer.key !== key) {
    const size = Math.min(900, Math.ceil(Math.min(canvas.width, canvas.height) / 1.3));
    renderTerrain(points, BASE_RECT, size, size, baseLayer.canvas);
    baseLayer.key = key; baseLayer.rect = BASE_RECT;
  }
  ctx.fillStyle = OCEAN;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  drawLayer(baseLayer);
  if (state.view.k >= 1.4 && detailLayer.rect && detailLayer.base === key) drawLayer(detailLayer);
  scheduleDetail();
}

function regionLabel(id) {
  const known = state.plans?.regions?.[id] || state.map?.regions?.find((r) => r.id === id) || {};
  if (known.name) return known.name;
  // Runs without mechanism names: the hypothesis' lead clause, when it has one, reads as a name.
  let text = known.hypothesis || id || "";
  const colon = text.indexOf(":");
  if (colon > 0 && colon <= 48) text = text.slice(0, colon);
  return text.length > 30 ? `${text.slice(0, 29)}…` : text;
}

function regionAnchors(points) {
  const groups = {};
  for (const p of points) {
    if (!p.region) continue;
    (groups[p.region] ||= []).push(p);
  }
  return Object.entries(groups).map(([id, list]) => {
    const coords = list.map((p) => toScreen(p.x, p.y));
    const cx = coords.reduce((a, c) => a + c[0], 0) / coords.length;
    const cy = coords.reduce((a, c) => a + c[1], 0) / coords.length;
    const radius = Math.max(18, ...coords.map(([x, y]) => Math.hypot(x - cx, y - cy)));
    return { id, cx, cy, radius, status: regionStatus(id), count: list.length };
  });
}

function drawMap() {
  if (!canvas.width) return;
  const empty = $("#map-empty");
  if (!state.map) { empty.textContent = "加载中…"; empty.classList.remove("hidden"); ctx.clearRect(0, 0, canvas.width, canvas.height); return; }
  if (!state.map.available) {
    empty.innerHTML = `${esc(state.map.reason)}<br>研究记录与运行监控仍可查看。`;
    empty.classList.remove("hidden");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  empty.classList.add("hidden");
  renderCensus();
  $("#map-note").textContent = state.map.note + (state.map.unplaced ? ` 另有 ${state.map.unplaced} 个未完成测量的尝试不定位。` : "");
  const points = visiblePoints();
  drawTerrain(points);
  const dpr = window.devicePixelRatio || 1;
  // Region labels and survey rings.
  ctx.textAlign = "center";
  const placed = [];
  const anchors = regionAnchors(points).sort((a, b) => b.count - a.count);
  for (const a of anchors) {
    const status = a.status?.status;
    const label = regionLabel(a.id);
    ctx.font = `600 ${12 * dpr}px "Noto Serif SC", Georgia, serif`;
    // Skip labels that would overlap a larger Region's label; hovering still names them.
    const half = ctx.measureText(label).width / 2 + 4 * dpr;
    const top = a.cy - a.radius - 22 * dpr, bottom = a.cy - a.radius + 10 * dpr;
    if (placed.some((b) => a.cx - half < b[2] && a.cx + half > b[0] && top < b[3] && bottom > b[1])) continue;
    placed.push([a.cx - half, top, a.cx + half, bottom]);
    ctx.lineWidth = 3.5 * dpr;
    ctx.strokeStyle = "rgba(10,16,22,.75)";
    ctx.fillStyle = "#f3ead2";
    const y = a.cy - a.radius - 8 * dpr;
    ctx.strokeText(label, a.cx, y); ctx.fillText(label, a.cx, y);
    if (status) {
      ctx.font = `${10.5 * dpr}px system-ui, sans-serif`;
      const text = `${MAP_STATUS[status]} · ${a.status.attempts} 次 · 均值 ${fmt(residualInfo(a.status), 4)}`;
      ctx.strokeText(text, a.cx, y + 13 * dpr);
      ctx.fillStyle = { land: "#bfe39b", saturated: "#e6cd98", seabed: "#9fd4f0", probing: "#d2bdf5" }[status];
      ctx.fillText(text, a.cx, y + 13 * dpr);
    }
  }
  // Points: admitted factors last so they stay visible.
  const latest = points.reduce((m, p) => Math.max(m, p.trial), 0);
  const order = [...points].sort((a, b) => (a.kind === "admitted") - (b.kind === "admitted"));
  const showCandidates = state.view.k >= 2;
  for (const p of order) {
    const [x, y] = toScreen(p.x, p.y);
    if (x < -20 || y < -20 || x > canvas.width + 20 || y > canvas.height + 20) continue;
    if (p.kind === "admitted") {
      // Admitted factors: a small gold dot with a dark ring, quiet even when clustered.
      ctx.beginPath(); ctx.arc(x, y, 3.2 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = "#f2c14e"; ctx.fill();
      ctx.lineWidth = 1.1 * dpr; ctx.strokeStyle = "rgba(40,28,6,.85)"; ctx.stroke();
    } else if (p.kind === "initial") {
      const r = 2.8 * dpr;
      ctx.fillStyle = "#f4f6f8"; ctx.strokeStyle = "#1d2630"; ctx.lineWidth = 1 * dpr;
      ctx.fillRect(x - r, y - r, 2 * r, 2 * r); ctx.strokeRect(x - r, y - r, 2 * r, 2 * r);
    } else if (showCandidates) {
      // Other attempts live in the terrain; only zoomed in do they appear, as faint specks.
      ctx.beginPath(); ctx.arc(x, y, 1.3 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(235,242,248,.35)"; ctx.fill();
    }
    if (p.trial === latest && latest > 0 && state.live) {
      ctx.beginPath(); ctx.arc(x, y, 9 * dpr, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(255,90,70,.9)"; ctx.lineWidth = 1.6 * dpr; ctx.stroke();
    }
    if (state.selected && state.selected.trial === p.trial && state.selected.kind === p.kind) {
      ctx.beginPath(); ctx.arc(x, y, 11 * dpr, 0, Math.PI * 2);
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2 * dpr; ctx.stroke();
    }
  }
  drawScale();
  updateTimeLabel();
}

function drawScale() {
  const dpr = window.devicePixelRatio || 1;
  const S = Math.min(canvas.width, canvas.height) * state.view.k;
  // 0.1 world units ~ the layout distance between factors with |corr| about 0.995 is not
  // meaningful; show a qualitative bar labelled as such.
  const len = 0.1 * S;
  const x = canvas.width - len - 80 * dpr, y = canvas.height - 26 * dpr;
  ctx.fillStyle = "rgba(10,16,22,.6)";
  ctx.fillRect(x - 8 * dpr, y - 16 * dpr, len + 16 * dpr, 24 * dpr);
  ctx.strokeStyle = "#e9e2cc"; ctx.lineWidth = 2 * dpr;
  ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + len, y); ctx.moveTo(x, y - 5 * dpr); ctx.lineTo(x, y); ctx.moveTo(x + len, y - 5 * dpr); ctx.lineTo(x + len, y); ctx.stroke();
  ctx.font = `${10 * dpr}px system-ui`; ctx.fillStyle = "#e9e2cc"; ctx.textAlign = "center";
  ctx.fillText("布局示意比例", x + len / 2, y - 6 * dpr);
}

function updateTimeLabel() {
  const total = Number($("#time").max);
  const admitted = state.map?.available ? visiblePoints().filter((p) => p.kind === "admitted").length : 0;
  $("#time-label").textContent = `第 ${state.time} / ${total} 次 · 已入库 ${admitted}`;
}

// ---------------------------------------------------------------- map interaction
let drag = null;
canvas.addEventListener("pointerdown", (e) => {
  drag = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y, moved: false };
  canvas.setPointerCapture(e.pointerId);
  canvas.classList.add("dragging");
});
canvas.addEventListener("pointermove", (e) => {
  const dpr = window.devicePixelRatio || 1;
  if (drag) {
    const dx = (e.clientX - drag.x) * dpr, dy = (e.clientY - drag.y) * dpr;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    state.view.x = drag.vx + dx; state.view.y = drag.vy + dy;
    scheduleMap();
    return;
  }
  const hit = hitTest(e);
  state.hover = hit;
  const tip = $("#tooltip");
  const region = hit ? null : hoverRegion(e);
  if (!hit && !region) { tip.classList.add("hidden"); return; }
  const r = canvas.getBoundingClientRect();
  tip.innerHTML = hit ? tooltipHtml(hit) : regionStatusHtml(region);
  tip.style.left = `${Math.min(r.width - 350, e.clientX - r.left + 14)}px`;
  tip.style.top = `${e.clientY - r.top + 14}px`;
  tip.classList.remove("hidden");
});
canvas.addEventListener("pointerup", (e) => {
  const moved = drag?.moved;
  drag = null;
  canvas.classList.remove("dragging");
  if (moved) return;
  const hit = hitTest(e);
  if (hit) { state.selected = hit; scheduleMap(); hit.kind === "initial" ? showInitial(hit) : showTrial(hit.trial); return; }
  const region = hitRegion(e);
  if (region) showRegion(region);
});
canvas.addEventListener("pointerleave", () => $("#tooltip").classList.add("hidden"));
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  zoomAt(e.offsetX * (window.devicePixelRatio || 1), e.offsetY * (window.devicePixelRatio || 1), e.deltaY < 0 ? 1.18 : 1 / 1.18);
}, { passive: false });

function zoomAt(sx, sy, factor) {
  const k = clamp(state.view.k * factor, 0.5, 14);
  const f = k / state.view.k;
  const S = Math.min(canvas.width, canvas.height);
  const cx = (canvas.width - S) / 2 + S / 2, cy = (canvas.height - S) / 2 + S / 2;
  state.view.x = sx - cx - (sx - cx - state.view.x) * f;
  state.view.y = sy - cy - (sy - cy - state.view.y) * f;
  state.view.k = k;
  scheduleMap();
}
$("#zoom-in").addEventListener("click", () => zoomAt(canvas.width / 2, canvas.height / 2, 1.4));
$("#zoom-out").addEventListener("click", () => zoomAt(canvas.width / 2, canvas.height / 2, 1 / 1.4));
$("#zoom-reset").addEventListener("click", () => { state.view = { k: 1, x: 0, y: 0 }; scheduleMap(); });

function hitTest(e) {
  if (!state.map?.available) return null;
  const dpr = window.devicePixelRatio || 1;
  const sx = e.offsetX * dpr, sy = e.offsetY * dpr;
  let best = null, dist = 9 * dpr;
  for (const p of visiblePoints()) {
    const [x, y] = toScreen(p.x, p.y);
    const d = Math.hypot(x - sx, y - sy);
    if (d < dist) { dist = d; best = p; }
  }
  return best;
}
function hitRegion(e) {
  const dpr = window.devicePixelRatio || 1;
  const sx = e.offsetX * dpr, sy = e.offsetY * dpr;
  return regionAnchors(visiblePoints()).find((a) => Math.hypot(a.cx - sx, a.cy - a.radius - 8 * dpr - sy) < 60 * dpr && Math.abs(a.cy - a.radius - 8 * dpr - sy) < 14 * dpr)?.id;
}
function hoverRegion(e) {
  if (!state.map?.available) return null;
  const dpr = window.devicePixelRatio || 1;
  const sx = e.offsetX * dpr, sy = e.offsetY * dpr;
  const anchors = regionAnchors(visiblePoints());
  const inside = anchors.filter((a) => Math.hypot(a.cx - sx, a.cy - sy) < a.radius + 6 * dpr)
    .sort((a, b) => a.radius - b.radius)[0];
  return inside?.id || hitRegion(e);
}
function tooltipHtml(p) {
  if (p.kind === "initial") return `<b>初始库成员</b><br>${esc(p.name || "")}`;
  return `<b>#${p.trial} ${esc(p.name || "")}</b><br>
    ${esc(REASON_TEXT[p.reason] || p.reason)}<br>
    Val IC ${signed(p.val)} · 残差 ${signed(p.residual)} · 库解释 ${fmt(p.library_r2, 2)}
    ${p.region ? `<hr>${regionStatusHtml(p.region)}` : ""}`;
}

// The full map state of one Region at the current time: its own state and every band.
function regionStatusHtml(id) {
  const s = regionStatus(id);
  const head = `<b>${esc(regionLabel(id))}</b>`;
  if (!s) return `${head}<br><span class="note">当前时刻尚无尝试</span>`;
  const rows = Object.entries(s.by_scale || {}).map(([key, b]) => {
    const label = SCALE_LABELS[key] || key;
    const chip = b.status && b.attempts ? `<span class="chip ${b.status}">${MAP_STATUS[b.status]}</span>` : '<span class="note">未测</span>';
    return `<tr><td>${label}</td><td>${chip}</td><td>${b.attempts}</td><td>${b.attempts ? signed(residualInfo(b)) : "—"}</td><td>${b.attempts ? bestOrAdmitted(b) : "—"}</td></tr>`;
  }).join("");
  return `${head} <span class="chip ${s.status}">${MAP_STATUS[s.status]}</span><br>
    尝试 ${s.attempts} · 残差信息/次 ${signed(residualInfo(s))} · ${bestOrAdmitted(s)}
    ${s.by_scale ? `<table class="scales"><tr><th>跨度</th><th>状态</th><th>尝试</th><th>残差/次</th><th>最佳/入库</th></tr>${rows}</table>` : ""}`;
}

const RULE_TEXT = {
  v2: { land: "曾有尝试残差高于海平面", saturated: "陆地，但最近尝试都低于海平面", seabed: "试满后无尝试高于海平面", probing: "尝试次数不足" },
  v3: { land: "该档有残差为正的入库因子；区域任一已测档为陆地", saturated: "陆地，但最近一轮尝试没有新入库", seabed: "试满后无此类入库；区域需已测各档全部关闭，未测档不计", probing: "尝试次数不足；区域有已测但未试满的档即为勘探中" },
  v3_region: { land: "区域有残差为正的入库因子", saturated: "陆地，但最近一轮尝试没有新入库", seabed: "试满后无此类入库", probing: "尝试次数不足" },
  v3_legacy: { land: "该档有残差为正的入库因子；区域任一档为陆地", saturated: "陆地，但最近一轮尝试没有新入库", seabed: "试满后无此类入库；区域需三档全部关闭", probing: "尝试次数不足；区域有未测完的档即为勘探中" },
};

// Census of Region and band states at the current time; hovering a chip lists its Regions.
function renderCensus() {
  const box = $("#map-census");
  if (!state.map?.available) { box.innerHTML = ""; return; }
  const rule = RULE_TEXT[state.map.rule] || RULE_TEXT.v2;
  document.querySelectorAll(".legend [data-rule]").forEach((el) => { el.textContent = rule[el.dataset.rule]; });
  const regions = {}, bands = {};
  let open = 0;
  for (const r of state.map.regions) {
    const s = regionStatus(r.id);
    if (!s) continue;
    (regions[s.status] ||= []).push(regionLabel(r.id));
    // Under the V3 rule an untested band does not keep a closed Region open.
    const statuses = Object.values(s.by_scale || {})
      .filter((b) => b.attempts || state.map.rule !== "v3")
      .map((b) => (b.attempts ? b.status : "probing"));
    for (const [key, b] of Object.entries(s.by_scale || {})) {
      const status = b.attempts ? b.status : "untested";
      (bands[status] ||= []).push(`${regionLabel(r.id)} · ${SCALE_LABELS[key] || key}`);
    }
    if ((s.status === "seabed" || s.status === "saturated") && statuses.some((x) => x === "probing" || x === "land")) open += 1;
  }
  const chips = (groups, labels) => Object.entries(labels).filter(([k]) => groups[k]).map(([k, label]) => {
    const names = groups[k];
    const title = names.slice(0, 40).join("\n") + (names.length > 40 ? `\n… 另 ${names.length - 40} 个` : "");
    return `<span class="chip ${k}" title="${esc(title)}">${label} ${names.length}</span>`;
  }).join(" ");
  const total = Object.values(regions).reduce((a, l) => a + l.length, 0);
  box.innerHTML = `<b>当前地图状态</b> <span class="note">第 ${state.time} 次 · 悬停查看名单</span><br>
    区域 ${total}：${chips(regions, MAP_STATUS)}<br>
    分档：${chips(bands, { ...MAP_STATUS, untested: "未测" })}
    ${open ? `<br><span class="note" title="区域层已关闭，但仍有勘探中或陆地分档">区域已关闭但分档未关闭：${open}</span>` : ""}`;
}

// ---------------------------------------------------------------- timeline
const slider = $("#time");
slider.addEventListener("input", () => {
  state.time = Number(slider.value);
  state.live = state.time === Number(slider.max) && $("#live").checked;
  scheduleMap();
});
$("#live").addEventListener("change", (e) => {
  state.live = e.target.checked;
  if (state.live) { state.time = Number(slider.max); slider.value = state.time; scheduleMap(); }
});
$("#play").addEventListener("click", () => {
  state.playing = !state.playing;
  $("#play").textContent = state.playing ? "❚❚" : "▶";
  if (state.playing) {
    state.live = false; $("#live").checked = false;
    if (state.time >= Number(slider.max)) state.time = 0;
    const step = Math.max(1, Math.round(Number(slider.max) / 240));
    const tick = () => {
      if (!state.playing) return;
      state.time = Math.min(Number(slider.max), state.time + step);
      slider.value = state.time;
      scheduleMap();
      if (state.time >= Number(slider.max)) { state.playing = false; $("#play").textContent = "▶"; return; }
      setTimeout(tick, 70);
    };
    tick();
  }
});

function drawSpark() {
  const c = $("#spark");
  const r = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  c.width = r.width * dpr; c.height = r.height * dpr;
  const g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  const curve = state.summary?.curve || [];
  if (!curve.length) return;
  const sea = state.summary.thresholds.sea_level;
  const max = Number(slider.max) || 1;
  const mid = c.height * 0.6;
  for (const row of curve) {
    const x = (row.trial / max) * c.width;
    if (row.accepted) { g.fillStyle = "#f2c14e"; g.fillRect(x - dpr, 0, 2 * dpr, c.height); continue; }
    if (row.residual === null || row.residual === undefined) continue;
    const h = clamp(row.residual / (sea * 3), -1, 1) * mid;
    g.fillStyle = row.residual >= sea ? "#9fd07a" : row.residual >= 0 ? "#5a7d8f" : "#7a4b4b";
    g.fillRect(x, h > 0 ? mid - h : mid, Math.max(1, dpr), Math.abs(h) || 1);
  }
}

// ---------------------------------------------------------------- detail panels
function openDetail(html) {
  $("#detail-body").innerHTML = html;
  $("#detail").classList.remove("collapsed");
}
function closeDetail() { $("#detail").classList.add("collapsed"); }
$("#detail-close").addEventListener("click", () => { closeDetail(); state.selected = null; scheduleMap(); });
$("#detail-body").addEventListener("click", (e) => {
  const link = e.target.closest("[data-plan],[data-trial],[data-region]");
  if (!link) return;
  if (link.dataset.plan) showPlan(Number(link.dataset.plan));
  else if (link.dataset.trial) showTrial(Number(link.dataset.trial));
  else if (link.dataset.region) showRegion(link.dataset.region);
});

// Same rows as the terminal: amount-weighted Pearson (primary, listed first) and Spearman.
function icTable(t) {
  const metrics = (t.feedback?.report?.metrics || []).filter((m) => m.name.includes("_weighted_"));
  const names = [...new Set(metrics.map((m) => m.name))];
  if (!names.length) return "";
  const value = (name, split) => metrics.find((m) => m.name === name && m.split === split)?.value;
  return `<table class="scales"><tr><th>√成交额加权</th><th>Train IC（原始）</th><th>Val IC（已定向）</th></tr>
    ${names.map((name, i) => `<tr><td>${name.includes("pearson") ? "Pearson" : "Spearman"}${i ? "" : " *"}</td>
      <td>${signed(value(name, "train"))}</td><td>${signed(value(name, "val"))}</td></tr>`).join("")}</table>
    <div class="note">* 主指标</div>`;
}

function metricsGrid(t) {
  return `${icTable(t)}<div class="metrics">
    <div><b>${signed(t.residual)}</b><span>残差 IC</span></div>
    <div><b>${fmt(t.library_r2, 3)}</b><span>库解释比例 R²</span></div>
    <div><b>${fmt(t.max_abs_corr, 3)}</b><span>最大库相关</span></div>
    <div><b>${fmt(t.coverage, 3)}</b><span>Val 覆盖</span></div>
    <div><b>${esc(REASON_TEXT[t.reason] || t.reason)}</b><span>准入结果</span></div></div>`;
}

async function showTrial(n) {
  const t = await api(`/api/trial?id=${encodeURIComponent(state.runId)}&n=${n}`);
  const point = state.map?.points?.find((p) => p.trial === n && p.kind !== "initial");
  if (point) { state.selected = point; scheduleMap(); }
  const plan = state.plans?.plans.find((p) => p.trials.some((x) => x.trial === n));
  const candidate = t.feedback.candidate || {};
  openDetail(`<h2>#${n} ${esc(t.name || "未命名候选")}</h2>
    <div>${t.accepted ? '<span class="chip ok">已入库</span>' : '<span class="chip no">未入库</span>'}
      ${plan ? ` <span class="link" data-plan="${plan.research_plan_index}">计划 #${plan.research_plan_index}</span> · <span class="link" data-region="${esc(plan.region_id)}">${esc(regionLabel(plan.region_id))}</span>` : ""}</div>
    <h4>表达式</h4><div class="expr">${esc(t.expression || "—")}</div>
    ${metricsGrid(t)}
    <h4>假说</h4><div>${esc(candidate.hypothesis || "—")}</div>
    <h4>其他</h4><div class="kv"><span>生成耗时</span><span>${fmt(t.generation_seconds, 1)} 秒</span>
      <span>评估耗时</span><span>${fmt(t.elapsed_seconds, 1)} 秒</span>
      <span>失败原因</span><span>${esc(t.feedback.report?.failure_reason || "—")}</span></div>`);
}

function showInitial(p) {
  openDetail(`<h2>初始库成员</h2><div>${esc(p.name || "")}</div>
    <p class="note">初始库是实验开始前已存在的因子，地图上作为已知大陆的地标。</p>`);
}

function trialsTable(trials) {
  return `<table class="trials"><tr><th>#</th><th>名称</th><th>Val</th><th>残差</th><th>结果</th></tr>
    ${trials.map((t) => `<tr data-trial="${t.trial}"><td>${t.trial}</td><td>${esc((t.name || t.expression || "").slice(0, 34))}</td>
      <td>${signed(t.val, 4)}</td><td>${signed(t.residual, 4)}</td><td>${t.accepted ? "◆" : esc(REASON_TEXT[t.reason] || t.reason)}</td></tr>`).join("")}</table>`;
}

function showPlan(index) {
  const plan = state.plans?.plans.find((p) => p.research_plan_index === index);
  if (!plan) return;
  const ex = plan.explorer;
  const proposals = ex ? ex.proposals.map((p, i) => `<div class="proposal ${i === ex.selected ? "selected" : ""}">
      <span class="score">${fmt(p.score, 0)}</span><b>${esc(p.objective)}</b>
      <div>${esc(p.hypothesis)}</div>
      <div class="note">预测：${esc(p.prediction)}<br>竞争解释：${esc(p.alternative)}<br>区域：${esc(p.region_id || p.new_region?.hypothesis || "")}</div></div>`).join("") : '<div class="note">本计划沿用前一次探索，或记录不含提案批次。</div>';
  const a = plan.annealing;
  const analyst = plan.plan ? Object.entries(plan.plan).filter(([k]) => !["region_id", "direction"].includes(k)) : [];
  openDetail(`<h2>研究计划 #${index}</h2>
    <div><span class="link" data-region="${esc(plan.region_id)}">${esc(regionLabel(plan.region_id))}</span></div>
    <h4>研究方向</h4><div>${esc(plan.direction)}</div>
    <h4>Explorer 提案</h4>${ex?.summary ? `<div class="note">${esc(ex.summary)}</div>` : ""}${proposals}
    <h4>Analyst 计划</h4><pre>${esc(analyst.map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v, null, 1)}`).join("\n\n"))}</pre>
    <h4>Miner 候选与平台评估</h4>${trialsTable(plan.trials)}
    ${a ? `<h4>退火裁决</h4><div class="kv"><span>结果</span><span>${a.accepted ? "接受" : "拒绝"}（${esc(a.reason)}）</span>
      <span>提案得分</span><span>${fmt(a.proposal_score, 3)}</span><span>当前得分</span><span>${fmt(a.incumbent_score, 3)}</span>
      <span>温度</span><span>${fmt(a.temperature, 3)}</span><span>接受概率</span><span>${fmt(a.acceptance_probability, 3)}</span>
      <span>之后的活动区域</span><span>${esc(a.active_region_after)}</span></div>` : ""}`);
}

// V3 states count admitted residual per attempt and admissions; V2 states the mean and best.
const residualInfo = (s) => s.admitted_residual_per_attempt ?? s.mean_residual_ic;
const bestOrAdmitted = (s) => (s.admitted !== undefined ? `入库 ${s.admitted}` : signed(s.best_residual_ic));
const SCALE_LABELS = {
  up_to_1h: "≤1 小时", "1h_to_1d": "1 小时–1 天", over_1d: "＞1 天",
  intraday: "日内（≤1 天）", "1d_to_20d": "1–20 天", over_20d: "＞20 天",
};
function scaleTable(bands) {
  const rows = Object.entries(bands).map(([key, b]) => {
    const label = SCALE_LABELS[key] || key;
    const chip = b.status && b.attempts ? `<span class="chip ${b.status}">${MAP_STATUS[b.status]}</span>` : '<span class="note">未测</span>';
    return `<tr><td>${label}</td><td>${chip}</td><td>${b.attempts}</td><td>${b.attempts ? signed(residualInfo(b)) : "—"}</td><td>${b.attempts ? bestOrAdmitted(b) : "—"}</td></tr>`;
  }).join("");
  return `<h4>按特征跨度分档 <small class="note">因子读取的交易时长，一天按 240 分钟</small></h4>
    <table class="scales"><tr><th>跨度</th><th>状态</th><th>尝试</th><th>残差信息/次</th><th>最佳/入库</th></tr>${rows}</table>`;
}

function showRegion(id) {
  const region = state.map?.regions?.find((r) => r.id === id);
  const status = regionStatus(id);
  const plans = (state.plans?.plans || []).filter((p) => p.region_id === id);
  const timeline = region?.timeline || [];
  const w = 380, h = 90;
  const sea = state.map?.sea_level ?? 0.005;
  const maxAbs = Math.max(sea * 2, ...timeline.map((s) => Math.abs(residualInfo(s))));
  const pts = timeline.map((s, i) => `${(i / Math.max(1, timeline.length - 1)) * w},${h / 2 - (residualInfo(s) / maxAbs) * (h / 2 - 4)}`).join(" ");
  openDetail(`<h2>${esc(regionLabel(id))}</h2><div class="note">${esc(id)}</div>
    ${status ? `<span class="chip ${status.status}">${MAP_STATUS[status.status]}</span> 尝试 ${status.attempts} · 残差信息/次 ${signed(residualInfo(status))} · ${bestOrAdmitted(status)}` : '<span class="note">当前时刻尚无尝试</span>'}
    ${region?.hypothesis ? `<h4>机制假说</h4><div>${esc(region.hypothesis)}</div>` : ""}
    ${status?.by_scale ? scaleTable(status.by_scale) : ""}
    <h4>平均残差信息的变化</h4>
    <svg viewBox="0 0 ${w} ${h}" style="width:100%;height:${h}px"><line x1="0" x2="${w}" y1="${h / 2}" y2="${h / 2}" stroke="#2c3b4b"/>
      <polyline points="${pts}" fill="none" stroke="#e8b84a" stroke-width="2"/></svg>
    <h4>研究计划</h4>${plans.map((p) => `<div class="link" data-plan="${p.research_plan_index}">#${p.research_plan_index} ${esc(p.direction?.slice(0, 80))}</div>`).join("") || '<div class="note">无</div>'}`);
}

// ---------------------------------------------------------------- plans tab
$("#plan-filter").addEventListener("input", renderPlans);
$("#plan-list").addEventListener("click", (e) => {
  const item = e.target.closest(".plan");
  if (item) showPlan(Number(item.dataset.plan));
});
function renderPlans() {
  const list = $("#plan-list");
  if (!state.plans) { list.innerHTML = '<div class="note">无研究计划记录。</div>'; return; }
  const filter = $("#plan-filter").value.trim().toLowerCase();
  const rows = [...state.plans.plans].reverse().filter((p) => !filter || JSON.stringify(p).toLowerCase().includes(filter));
  list.innerHTML = rows.map((p) => {
    const admitted = p.trials.filter((t) => t.accepted).length;
    const best = Math.max(...p.trials.map((t) => t.residual ?? -Infinity));
    const status = state.map?.available ? regionStatus(p.region_id)?.status : null;
    return `<div class="plan" data-plan="${p.research_plan_index}">
      <div class="idx">#${p.research_plan_index}</div>
      <div><div class="dir">${esc(p.direction)}</div>
        <div class="tags"><span title="${esc(p.region_id)}">${esc(regionLabel(p.region_id))}</span>${status ? `<span class="chip ${status}">${MAP_STATUS[status]}</span>` : ""}
          ${p.annealing ? `<span>${p.annealing.accepted ? "退火接受" : "退火拒绝"}</span>` : ""}</div></div>
      <div class="side">${p.trials.length} 个候选<br>${admitted ? `◆ ${admitted} 入库` : "未入库"}<br>${Number.isFinite(best) ? `最佳残差 ${fmt(best)}` : ""}</div></div>`;
  }).join("") + (state.plans.blocked.length ? `<h3>受阻计划</h3>${state.plans.blocked.map((b) => `<div class="plan"><div class="idx">#${b.research_plan_index}</div><div>${esc(b.direction)}<div class="tags">${esc(b.blocked)}</div></div><div></div></div>`).join("")}` : "");
}

// ---------------------------------------------------------------- monitor tab
function lineChart(svg, series, { yLabel = "", baseline = null } = {}) {
  const r = svg.getBoundingClientRect();
  const w = r.width || 600, h = r.height || 220, pad = 36;
  const all = series.flatMap((s) => s.points.map((p) => p[1])).concat(baseline ?? []);
  const xs = series.flatMap((s) => s.points.map((p) => p[0]));
  if (!xs.length) { svg.innerHTML = ""; return; }
  const x0 = Math.min(...xs), x1 = Math.max(...xs, x0 + 1);
  const y0 = Math.min(0, ...all), y1 = Math.max(...all, y0 + 1e-9);
  const X = (v) => pad + ((v - x0) / (x1 - x0)) * (w - pad - 10);
  const Y = (v) => h - 22 - ((v - y0) / (y1 - y0)) * (h - 34);
  let out = `<line x1="${pad}" x2="${w - 10}" y1="${Y(0)}" y2="${Y(0)}" stroke="#2c3b4b"/>`;
  for (const t of [y0, (y0 + y1) / 2, y1]) out += `<text x="4" y="${Y(t) + 4}" fill="#8a9aab" font-size="10">${t.toFixed(Math.abs(y1) < 1 ? 3 : 0)}</text>`;
  if (baseline !== null) out += `<line x1="${pad}" x2="${w - 10}" y1="${Y(baseline)}" y2="${Y(baseline)}" stroke="#9fd07a" stroke-dasharray="5 4"/>`;
  for (const s of series) {
    if (s.dots) {
      out += s.points.map(([x, y, c]) => `<circle cx="${X(x)}" cy="${Y(y)}" r="${c === "#f2c14e" ? 3.4 : 2}" fill="${c || s.color}"/>`).join("");
    } else {
      out += `<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${s.points.map(([x, y]) => `${X(x)},${Y(y)}`).join(" ")}"/>`;
      const last = s.points[s.points.length - 1];
      out += `<text x="${X(last[0]) - 4}" y="${Y(last[1]) - 6}" text-anchor="end" fill="${s.color}" font-size="11">${s.label}</text>`;
    }
  }
  out += `<text x="${w - 12}" y="${h - 6}" text-anchor="end" fill="#8a9aab" font-size="10">尝试次数 ${x1}</text>`;
  out += `<text x="${pad}" y="12" fill="#8a9aab" font-size="10">${yLabel}</text>`;
  svg.innerHTML = out;
}

function renderMonitor() {
  const s = state.summary;
  if (!s || state.tab !== "monitor") return;
  const curve = s.curve;
  const maxAdmitted = Math.max(1, ...curve.map((c) => c.admitted));
  const maxInfo = Math.max(1e-6, ...curve.map((c) => Math.abs(c.information)));
  lineChart($("#chart-growth"), [
    { label: `入库 ${s.admitted}`, color: "#f2c14e", points: curve.map((c) => [c.trial, c.admitted / maxAdmitted]) },
    ...(s.residual ? [{ label: `入库因子残差 ${fmt(curve.at(-1)?.information, 3)}`, color: "#7cc1e6", points: curve.map((c) => [c.trial, c.information / maxInfo]) }] : []),
  ], { yLabel: "归一化（各自最大值为 1）" });
  if (s.residual) {
    lineChart($("#chart-residual"), [{
      dots: true, color: "#5a7d8f",
      points: curve.filter((c) => c.residual !== null && c.residual !== undefined).map((c) => [c.trial, c.residual, c.accepted ? "#f2c14e" : c.residual >= s.thresholds.sea_level ? "#9fd07a" : "#5a7d8f"]),
    }], { yLabel: "残差 IC", baseline: s.thresholds.sea_level });
  } else {
    lineChart($("#chart-residual"), [{
      dots: true, color: "#5a7d8f",
      points: curve.filter((c) => c.val !== null && c.val !== undefined).map((c) => [c.trial, c.val, c.accepted ? "#f2c14e" : "#5a7d8f"]),
    }], { yLabel: "该运行未开启残差：显示 Val IC" });
  }
  const total = Math.max(1, s.completed);
  $("#reasons").innerHTML = `<div class="bars">${Object.entries(s.reasons).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
    `<div class="b"><span>${esc(REASON_TEXT[k] || k)}</span><i class="${k === "accepted" ? "accepted" : ""}" style="width:${(100 * v) / total}%"></i><span>${v}</span></div>`).join("")}</div>`;
  const c = s.current;
  $("#current").innerHTML = c ? `<div class="kv"><span>计划</span><span class="link" onclick="showPlan(${c.research_plan_index})">#${c.research_plan_index}</span>
    <span>区域</span><span>${esc(c.region_id || "选题中")}</span><span>模式</span><span>${esc(c.mode || "—")}</span>
    <span>温度</span><span>${fmt(c.temperature, 3)}</span><span>已提交候选</span><span>${c.candidates}</span>
    <span>方向</span><span>${esc(c.direction || "Explorer 正在选题")}</span><span>活动区域</span><span>${esc(s.active_region || "—")}</span></div>`
    : `<div class="note">当前没有进行中的计划${s.status === "frozen" ? "（运行已冻结）" : ""}。</div>`;
  const seconds = s.total_seconds || (curve.at(-1)?.seconds ?? 0);
  const rate = seconds ? (s.completed / seconds) * 3600 : 0;
  const remaining = s.attempts && rate ? (s.attempts - s.completed) / rate : null;
  const u = s.usage || {};
  $("#usage").innerHTML = `<div class="kv"><span>活动时间</span><span>${(seconds / 3600).toFixed(2)} 小时</span>
    <span>速度</span><span>${rate.toFixed(1)} 次/小时</span>
    <span>预计剩余</span><span>${remaining === null || s.status !== "running" ? "—" : `${remaining.toFixed(1)} 小时`}</span>
    <span>模型请求</span><span>${u.chat_requests ?? "—"}</span>
    <span>输入 tokens</span><span>${(u.prompt_tokens ?? 0).toLocaleString()}</span>
    <span>输出 tokens</span><span>${(u.completion_tokens ?? 0).toLocaleString()}</span>
    <span>最后活动</span><span>${ago(s.last_activity, s.now)}</span></div>`;
  $("#config").innerHTML = `<div class="kv">${Object.entries(s.method_config || {}).map(([k, v]) => `<span>${esc(k)}</span><span>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>`).join("")}</div>`;
}

document.querySelector(".legend-title").addEventListener("click", () =>
  document.querySelector(".legend").classList.toggle("folded"));

// ---------------------------------------------------------------- start
window.showPlan = showPlan;
// A shared link may open one detail panel once, before the hash is rewritten.
state.linkedDetail = new URLSearchParams(location.hash.slice(1));
resize();
switchTab(new URLSearchParams(location.hash.slice(1)).get("tab") || "map");
loadRuns();
setInterval(loadRuns, 15000);
setInterval(() => refresh(false), 8000);
