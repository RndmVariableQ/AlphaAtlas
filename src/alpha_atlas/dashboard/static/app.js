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
        <div class="row1"><span class="dot ${live}"></span>${esc(r.method)} · ${esc(r.model || "无模型")}${r.residual ? ' <span class="chip land">地图</span>' : ""}</div>
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
  $("#run-meta").innerHTML = [s.asset, s.fold, s.version, s.run_id].filter(Boolean).map((v) => `<span>${esc(v)}</span>`).join("");
  const info = s.curve.length ? s.curve[s.curve.length - 1].information : 0;
  $("#run-stats").innerHTML = [
    ["完成 / 预算", `${s.completed} / ${s.attempts ?? "?"}`],
    ["新增入库", s.admitted],
    ["库规模", s.library_size],
    ["累计残差信息", s.residual ? fmt(info, 3) : "未开启"],
    ["最后活动", ago(s.last_activity, s.now)],
    ...(s.stop_reason ? [["提前结束", s.stop_reason]] : []),
  ].map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${k}</span></div>`).join("");
  $("#run-progress").style.width = `${s.attempts ? Math.min(100, (100 * s.completed) / s.attempts) : 0}%`;
}

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
const terrain = document.createElement("canvas");
const tctx = terrain.getContext("2d");
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
const LAND = [
  [0, [214, 199, 151]], [0.04, [168, 170, 112]], [0.12, [108, 142, 78]], [0.3, [84, 118, 62]],
  [0.5, [128, 116, 80]], [0.68, [139, 120, 98]], [0.82, [150, 146, 140]], [0.94, [236, 238, 240]], [1, [252, 252, 252]],
];
const WATER = [[0, [118, 196, 200]], [0.18, [70, 158, 182]], [0.45, [34, 104, 148]], [0.75, [17, 62, 108]], [1, [8, 32, 62]]];
const DRY = [181, 156, 108];
const PEAK = 1.6;

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

function renderTerrain(points) {
  const gw = Math.min(560, Math.ceil(canvas.width / 2.5));
  const gh = Math.min(400, Math.ceil(canvas.height / 2.5));
  terrain.width = gw; terrain.height = gh;
  const density = new Float32Array(gw * gh), heightSum = new Float32Array(gw * gh), dry = new Float32Array(gw * gh);
  const peak = new Float32Array(gw * gh).fill(-1e9);
  const S = Math.min(canvas.width, canvas.height) * state.view.k;
  const sigmaPx = Math.max(6, 0.032 * S) * (gw / canvas.width);
  const reach = Math.ceil(sigmaPx * 3);
  const status = {};
  for (const p of points) {
    const e = elevation(p);
    if (e === null) continue;
    if (p.region && !(p.region in status)) status[p.region] = regionStatus(p.region)?.status;
    const [sx, sy] = toScreen(p.x, p.y);
    const gx = (sx / canvas.width) * gw, gy = (sy / canvas.height) * gh;
    const saturated = status[p.region] === "saturated" ? 1 : 0;
    const x0 = Math.max(0, Math.floor(gx - reach)), x1 = Math.min(gw - 1, Math.ceil(gx + reach));
    const y0 = Math.max(0, Math.floor(gy - reach)), y1 = Math.min(gh - 1, Math.ceil(gy + reach));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const d2 = (x - gx) ** 2 + (y - gy) ** 2;
        const w = Math.exp(-d2 / (2 * sigmaPx * sigmaPx));
        const i = y * gw + x;
        density[i] += w; heightSum[i] += w * e; dry[i] += w * saturated;
        // Each factor also raises its own hill, so one strong factor is not averaged away
        // by many ordinary neighbours; the hill falls below the mean within about one sigma.
        const hill = e - PEAK * d2 / (sigmaPx * sigmaPx);
        if (hill > peak[i]) peak[i] = hill;
      }
    }
  }
  const H = new Float32Array(gw * gh), explored = new Float32Array(gw * gh);
  for (let y = 0; y < gh; y++) {
    for (let x = 0; x < gw; x++) {
      const i = y * gw + x;
      const [wx, wy] = toWorld((x / gw) * canvas.width, (y / gh) * canvas.height);
      // Domain-warped noise roughens coastlines and shelf edges; it is texture, not data.
      const warp = fbm(wx * 6 + 5.2, wy * 6 - 1.3, 4);
      const n = fbm(wx * 11 + 2 * warp, wy * 11 - 2 * warp);
      const raw = 1 - Math.exp(-density[i] / 0.55);
      const m = clamp(raw + 1.1 * n * raw * (1 - raw), 0, 1);
      explored[i] = m;
      const mean = density[i] > 1e-4 ? heightSum[i] / density[i] : -3;
      const top = Math.max(mean, peak[i]);
      const base = top + Math.log(Math.exp(4 * (mean - top)) + Math.exp(4 * (peak[i] - top))) / 4;
      // Measured elevation inside explored areas; abyssal plain elsewhere. Noise is scaled by
      // exploration so it never lifts unexplored sea above water.
      H[i] = m * (base + (0.5 + 0.6 * (1 - m)) * n * m + 0.25 * fbm(wx * 34, wy * 34, 3) * m)
        + (1 - m) * (-3.4 + 0.5 * n);
    }
  }
  const image = tctx.createImageData(gw, gh);
  const data = image.data;
  const light = [-0.55, -0.65, 0.52];
  for (let y = 0; y < gh; y++) {
    for (let x = 0; x < gw; x++) {
      const i = y * gw + x;
      const h = H[i], m = explored[i];
      const hx = H[y * gw + Math.min(gw - 1, x + 1)] - H[y * gw + Math.max(0, x - 1)];
      const hy = H[Math.min(gh - 1, y + 1) * gw + x] - H[Math.max(0, y - 1) * gw + x];
      const z = 3.2;
      const nx = -hx * z, ny = -hy * z, nz = 1;
      const len = Math.hypot(nx, ny, nz);
      const shade = clamp((nx * light[0] + ny * light[1] + nz * light[2]) / len, 0, 1);
      const [wx, wy] = toWorld((x / gw) * canvas.width, (y / gh) * canvas.height);
      let c;
      if (h >= 0) {
        c = ramp(LAND, h / 4.2);
        const d = density[i] > 1e-4 ? dry[i] / density[i] : 0;
        if (d > 0) c = [mix(c[0], DRY[0], d * 0.75), mix(c[1], DRY[1], d * 0.75), mix(c[2], DRY[2], d * 0.75)];
        const relief = 0.5 + 0.75 * shade;
        c = c.map((v) => v * relief);
        const contour = Math.abs(((h / 0.6) % 1) - 0.5);
        if (h > 0.3 && contour > 0.47) c = c.map((v) => v * 0.86);
        if (h < 0.07) c = c.map((v, k) => mix(v, [92, 82, 58][k], 0.35 * (1 - h / 0.07)));
      } else {
        c = ramp(WATER, -h / 3.6);
        // Explored seabed keeps visible bathymetric relief; open sea stays smooth.
        const relief = 0.82 + 0.32 * shade * m;
        c = c.map((v) => v * relief);
        if (h > -0.28 && m > 0.15) {
          const foam = (1 + h / 0.28) * 0.38 * (0.75 + 0.25 * valueNoise(wx * 260, wy * 260));
          c = c.map((v) => mix(v, 236, foam));
        }
        const ripple = 0.03 * fbm(wx * 60 + 3, wy * 60) ;
        c = c.map((v) => v * (1 + ripple));
      }
      // Fog of war: drifting cloud banks over unexplored sea, thinning toward explored water.
      const unexplored = smooth(0.32, 0.02, m);
      const cloud = smooth(-0.08, 0.38, fbm(wx * 4.2 + 11, wy * 4.2 - 7, 5) + 0.12 * fbm(wx * 17, wy * 17, 3));
      const fog = unexplored * (0.08 + 0.5 * cloud);
      c = [mix(c[0], 222, fog), mix(c[1], 228, fog), mix(c[2], 234, fog)];
      const o = i * 4;
      data[o] = c[0]; data[o + 1] = c[1]; data[o + 2] = c[2]; data[o + 3] = 255;
    }
  }
  tctx.putImageData(image, 0, 0);
}

function regionLabel(id) {
  const text = state.plans?.regions?.[id] || state.map?.regions?.find((r) => r.id === id)?.hypothesis || id || "";
  return text.length > 26 ? `${text.slice(0, 25)}…` : text;
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
  $("#map-note").textContent = state.map.note + (state.map.unplaced ? ` 另有 ${state.map.unplaced} 个未完成测量的尝试不定位。` : "");
  const points = visiblePoints();
  renderTerrain(points);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(terrain, 0, 0, canvas.width, canvas.height);
  const dpr = window.devicePixelRatio || 1;
  // Region labels and survey rings.
  ctx.textAlign = "center";
  for (const a of regionAnchors(points)) {
    const status = a.status?.status;
    if (status === "probing") {
      ctx.setLineDash([5 * dpr, 4 * dpr]);
      ctx.strokeStyle = "rgba(190,160,240,.9)";
      ctx.lineWidth = 1.4 * dpr;
      ctx.beginPath(); ctx.arc(a.cx, a.cy, a.radius + 10 * dpr, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
    }
    const label = regionLabel(a.id);
    ctx.font = `600 ${12 * dpr}px "Noto Serif SC", Georgia, serif`;
    ctx.lineWidth = 3.5 * dpr;
    ctx.strokeStyle = "rgba(10,16,22,.75)";
    ctx.fillStyle = "#f3ead2";
    const y = a.cy - a.radius - 8 * dpr;
    ctx.strokeText(label, a.cx, y); ctx.fillText(label, a.cx, y);
    if (status) {
      ctx.font = `${10.5 * dpr}px system-ui, sans-serif`;
      const text = `${MAP_STATUS[status]} · ${a.status.attempts} 次 · 均值 ${fmt(a.status.mean_residual_ic, 4)}`;
      ctx.strokeText(text, a.cx, y + 13 * dpr);
      ctx.fillStyle = { land: "#bfe39b", saturated: "#e6cd98", seabed: "#9fd4f0", probing: "#d2bdf5" }[status];
      ctx.fillText(text, a.cx, y + 13 * dpr);
    }
  }
  // Points: admitted factors last so they stay visible.
  const latest = points.reduce((m, p) => Math.max(m, p.trial), 0);
  const order = [...points].sort((a, b) => (a.kind === "admitted") - (b.kind === "admitted"));
  for (const p of order) {
    const [x, y] = toScreen(p.x, p.y);
    if (x < -20 || y < -20 || x > canvas.width + 20 || y > canvas.height + 20) continue;
    if (p.kind === "admitted") {
      const r = 5.5 * dpr;
      ctx.beginPath(); ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath();
      ctx.fillStyle = "#f2c14e"; ctx.fill(); ctx.lineWidth = 1.2 * dpr; ctx.strokeStyle = "#3b2a08"; ctx.stroke();
    } else if (p.kind === "initial") {
      const r = 3.6 * dpr;
      ctx.fillStyle = "#f4f6f8"; ctx.strokeStyle = "#1d2630"; ctx.lineWidth = 1 * dpr;
      ctx.fillRect(x - r, y - r, 2 * r, 2 * r); ctx.strokeRect(x - r, y - r, 2 * r, 2 * r);
    } else {
      const above = (p.residual ?? -1) >= state.map.sea_level;
      ctx.beginPath(); ctx.arc(x, y, (above ? 2.8 : 2.1) * dpr, 0, Math.PI * 2);
      ctx.fillStyle = p.offshore ? "rgba(30,40,52,.0)" : above ? "rgba(255,255,255,.92)" : "rgba(24,36,48,.78)";
      ctx.fill();
      ctx.lineWidth = 1 * dpr;
      ctx.strokeStyle = p.offshore ? "rgba(40,55,70,.85)" : above ? "rgba(30,40,30,.8)" : "rgba(220,235,245,.55)";
      ctx.stroke();
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
  if (!hit) { tip.classList.add("hidden"); return; }
  const r = canvas.getBoundingClientRect();
  tip.innerHTML = tooltipHtml(hit);
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
function tooltipHtml(p) {
  if (p.kind === "initial") return `<b>初始库成员</b><br>${esc(p.name || "")}`;
  return `<b>#${p.trial} ${esc(p.name || "")}</b><br>
    ${esc(REASON_TEXT[p.reason] || p.reason)} · 区域 ${esc(p.region || "—")}<br>
    Val IC ${signed(p.val)} · 残差 ${signed(p.residual)} · 库解释 ${fmt(p.library_r2, 2)}
    ${p.offshore ? "<br><i>外海：与全库相关性低，位置仅按机制分组</i>" : ""}`;
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

function metricsGrid(t) {
  return `<div class="metrics">
    <div><b>${signed(t.val)}</b><span>Val IC（已定向）</span></div>
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
  const train = (t.feedback.report?.metrics || []).find((m) => m.split === "train");
  openDetail(`<h2>#${n} ${esc(t.name || "未命名候选")}</h2>
    <div>${t.accepted ? '<span class="chip ok">已入库</span>' : '<span class="chip no">未入库</span>'}
      ${plan ? ` <span class="link" data-plan="${plan.research_plan_index}">计划 #${plan.research_plan_index}</span> · <span class="link" data-region="${esc(plan.region_id)}">${esc(regionLabel(plan.region_id))}</span>` : ""}</div>
    <h4>表达式</h4><div class="expr">${esc(t.expression || "—")}</div>
    ${metricsGrid(t)}
    <h4>假说</h4><div>${esc(candidate.hypothesis || "—")}</div>
    <h4>其他</h4><div class="kv"><span>Train IC</span><span>${signed(train?.value)}</span>
      <span>生成耗时</span><span>${fmt(t.generation_seconds, 1)} 秒</span>
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

function showRegion(id) {
  const region = state.map?.regions?.find((r) => r.id === id);
  const status = regionStatus(id);
  const plans = (state.plans?.plans || []).filter((p) => p.region_id === id);
  const timeline = region?.timeline || [];
  const w = 380, h = 90;
  const sea = state.map?.sea_level ?? 0.005;
  const maxAbs = Math.max(sea * 2, ...timeline.map((s) => Math.abs(s.mean_residual_ic)));
  const pts = timeline.map((s, i) => `${(i / Math.max(1, timeline.length - 1)) * w},${h / 2 - (s.mean_residual_ic / maxAbs) * (h / 2 - 4)}`).join(" ");
  openDetail(`<h2>${esc(regionLabel(id))}</h2><div class="note">${esc(id)}</div>
    ${status ? `<span class="chip ${status.status}">${MAP_STATUS[status.status]}</span> 尝试 ${status.attempts} · 平均残差 ${signed(status.mean_residual_ic)} · 最佳 ${signed(status.best_residual_ic)}` : '<span class="note">当前时刻尚无尝试</span>'}
    ${region?.hypothesis ? `<h4>机制假说</h4><div>${esc(region.hypothesis)}</div>` : ""}
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
    ...(s.residual ? [{ label: `累计残差信息 ${fmt(curve.at(-1)?.information, 3)}`, color: "#7cc1e6", points: curve.map((c) => [c.trial, c.information / maxInfo]) }] : []),
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
