/* Mission Control v4 — ops console client.
 *
 * Correctness rules preserved from the v3 build:
 *  - A value that is unavailable renders as an explicit "no data" marker,
 *    never as 0 and never as a blank cell (num() strictness).
 *  - Every server-supplied string is escaped before it reaches innerHTML.
 *  - Every panel can fail independently (Promise.allSettled); one dead
 *    endpoint never blanks the page.
 *  - Heterogeneous goal lanes render whichever schema is present.
 *  - Gate tables keep "not computable" honesty.
 * New in v4: ops rail (9 audited endpoints), confirm dialog with exact-phrase
 * friction whose Cancel/Esc always resolves, value-change ticks, per-panel
 * sync ages, heartbeat pulse, SVG equity chart with tooltip, WR band strips,
 * wallet delta bars, position bracket hero, tablist keyboard support.
 */
(() => {
  "use strict";

  const TOKEN_KEY = "mission_control_token";
  const REFRESH_MS = 12000;
  const NA = '<span class="na" title="No data available from the bot\'s state files">no data</span>';

  const state = {
    status: null, positions: null, risk: null, funnel: null, goals: null,
    gates: null, tasks: null, audit: null, candidates: null, candFilter: null,
    brain: null, brainFeedAll: false,
    errors: {}, fetchedAt: {}, lastRefreshAt: null, inFlight: false,
    autoRefresh: true, taskFilter: "", tasksLoaded: false,
    historyFilter: "", historyRange: "all",
    historySort: { key: "close_time", dir: -1 },
    openLanes: new Set(),
    opsBusy: null, opsSig: "",
    booted: false,
  };

  const $ = (id) => document.getElementById(id);
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- primitives ---------- */

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Strict numeric coercion. Unlike Number(), "" and null do NOT become 0.
  function num(v) {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function fmtNum(v, digits = 2) {
    const n = num(v);
    if (n === null) return null;
    return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  }

  // Precision derived from magnitude: a $0.72 altcoin stop must not collapse
  // to "0.72" and look identical to its entry.
  function fmtPrice(v) {
    const n = num(v);
    if (n === null) return null;
    const a = Math.abs(n);
    const digits = a >= 1000 ? 4 : a >= 1 ? 6 : 8;
    return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  }

  function fmtMoney(v, digits = 2) {
    const n = num(v);
    if (n === null) return null;
    const sign = n < 0 ? "−" : "";
    return `${sign}$${Math.abs(n).toLocaleString(undefined, {
      maximumFractionDigits: digits, minimumFractionDigits: 2,
    })}`;
  }

  function fmtSignedMoney(v, digits = 2) {
    const n = num(v);
    if (n === null) return null;
    return (n > 0 ? "+" : "") + fmtMoney(n, digits);
  }

  function fmtPct(v, digits = 1) {
    const n = num(v);
    if (n === null) return null;
    return `${(n * 100).toFixed(digits)}%`;
  }

  function cell(text) {
    return text === null || text === undefined ? NA : esc(text);
  }

  function fmtAge(seconds) {
    const s = num(seconds);
    if (s === null) return null;
    const r = Math.round(s);
    if (r < 60) return `${r}s`;
    if (r < 3600) return `${Math.floor(r / 60)}m ${r % 60}s`;
    if (r < 86400) return `${Math.floor(r / 3600)}h ${Math.floor((r % 3600) / 60)}m`;
    return `${Math.floor(r / 86400)}d ${Math.floor((r % 86400) / 3600)}h`;
  }

  function fmtUptime(seconds) {
    const s = num(seconds);
    if (s === null) return null;
    const t = Math.max(0, Math.floor(s));
    return `${Math.floor(t / 3600)}h ${Math.floor((t % 3600) / 60)}m`;
  }

  function fmtEpoch(epoch) {
    const n = num(epoch);
    if (n === null) return null;
    return new Date(n * 1000).toLocaleString();
  }

  function fmtIsoLocal(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
  }

  function isoAgeSeconds(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return Math.max(0, (Date.now() - d.getTime()) / 1000);
  }

  function toast(message, kind = "ok", ms = 4200, sub = null) {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    if (sub) {
      const s = document.createElement("span");
      s.className = "t-sub";
      s.textContent = sub;
      el.appendChild(s);
    }
    $("toast-host").appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  // data-mk marks a value for change-tick animation; data-raw carries the
  // numeric for direction. Gated by prefers-reduced-motion.
  function metricCard(label, value, opts = {}) {
    const v = value === null || value === undefined ? NA : esc(value);
    const cls = opts.cls ? ` ${opts.cls}` : "";
    const sub = opts.sub ? `<div class="sub">${esc(opts.sub)}</div>` : "";
    const title = opts.title ? ` title="${esc(opts.title)}"` : "";
    const mk = opts.mk ? ` data-mk="${esc(opts.mk)}"` : "";
    const raw = opts.raw !== undefined && opts.raw !== null ? ` data-raw="${esc(opts.raw)}"` : "";
    return `<div class="metric${cls}"${title}><div class="k">${esc(label)}</div><div class="v"${mk}${raw}>${v}</div>${sub}</div>`;
  }

  const prevVals = new Map();
  function applyTicks(scope) {
    (scope || document).querySelectorAll("[data-mk]").forEach((el) => {
      const key = el.getAttribute("data-mk");
      const text = el.textContent;
      const prev = prevVals.get(key);
      prevVals.set(key, { text, raw: num(el.getAttribute("data-raw")) });
      if (prev === undefined || prev.text === text || REDUCED) return;
      const raw = num(el.getAttribute("data-raw"));
      el.classList.add("tick");
      if (raw !== null && prev.raw !== null) el.classList.add(raw > prev.raw ? "up" : "down");
      setTimeout(() => el.classList.remove("tick", "up", "down"), 1000);
    });
  }

  function freshnessLine(fileMeta, generatedUtc, cadenceHintSeconds) {
    const genAge = isoAgeSeconds(generatedUtc);
    const age = genAge !== null ? genAge : (fileMeta && num(fileMeta.age_seconds));
    if (age === null || age === undefined) return `<span class="na">data age unknown</span>`;
    const stale = cadenceHintSeconds && age > cadenceHintSeconds * 2;
    const when = generatedUtc ? fmtIsoLocal(generatedUtc) : fmtIsoLocal(fileMeta && fileMeta.mtime_utc);
    return `<span class="${stale ? "stale" : "fresh"}">data computed ${esc(fmtAge(age))} ago${when ? ` · ${esc(when)}` : ""}</span>`;
  }

  /* ---------- token + transport ---------- */

  const getToken = () => sessionStorage.getItem(TOKEN_KEY) || "";
  const setToken = (t) => sessionStorage.setItem(TOKEN_KEY, t);

  let tokenPrompt = null;

  // Single-flight: eight parallel 401s must not each call showModal().
  function ensureToken(force = false) {
    if (!force && getToken()) return Promise.resolve(getToken());
    if (tokenPrompt) return tokenPrompt;
    const dialog = $("token-dialog");
    const input = $("token-input");
    input.value = "";
    tokenPrompt = new Promise((resolve) => {
      const done = (value) => {
        dialog.removeEventListener("close", onClose);
        $("token-form").onsubmit = null;
        tokenPrompt = null;
        resolve(value);
      };
      const onClose = () => done(getToken());
      dialog.addEventListener("close", onClose);
      $("token-form").onsubmit = () => {
        const v = input.value.trim();
        if (v) setToken(v);
      };
      if (!dialog.open) dialog.showModal();
    });
    return tokenPrompt;
  }

  function authHeaders() {
    const token = getToken();
    const headers = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  }

  function parseDetail(data, res) {
    let d = data.detail || data.error;
    if (Array.isArray(d)) d = d.map((e) => `${(e.loc || []).join(".")}: ${e.msg}`).join("; ");
    return d || res.statusText || "request failed";
  }

  async function api(path) {
    const res = await fetch(path, { headers: authHeaders() });
    if (res.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      await ensureToken(true);
      throw new Error("unauthorized — token rejected");
    }
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!res.ok) throw new Error(parseDetail(data, res));
    return data;
  }

  async function apiPost(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (res.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      await ensureToken(true);
      throw new Error("unauthorized — token rejected");
    }
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!res.ok) throw new Error(parseDetail(data, res));
    return data;
  }

  /* ---------- shared tooltip ---------- */

  const tip = () => $("tip");
  function showTip(html, x, y) {
    const t = tip();
    t.innerHTML = html;
    t.hidden = false;
    const pad = 14;
    const w = t.offsetWidth, h = t.offsetHeight;
    let left = x + pad, top = y - h - pad;
    if (left + w > window.innerWidth - 8) left = x - w - pad;
    if (top < 8) top = y + pad;
    t.style.left = `${Math.max(8, left)}px`;
    t.style.top = `${top}px`;
  }
  function hideTip() { tip().hidden = true; }

  /* ---------- labels ---------- */

  function sideLabel(side) {
    const s = String(side || "").toLowerCase();
    if (s === "buy" || s === "long") return { text: "LONG", cls: "side-buy", pill: "long" };
    if (s === "sell" || s === "short") return { text: "SHORT", cls: "side-sell", pill: "short" };
    return { text: side ? String(side) : null, cls: "", pill: "" };
  }

  // Windows Task Scheduler result codes, including the HRESULTs this box emits.
  const TASK_CODES = {
    0: { text: "OK", cls: "ok" },
    1: { text: "Exit 1 — incorrect function", cls: "danger" },
    2: { text: "Exit 2 — file not found", cls: "danger" },
    267009: { text: "Currently running", cls: "info" },
    267011: { text: "Never ran", cls: "warn" },
    267014: { text: "Terminated by user", cls: "warn" },
    2147942401: { text: "0x80070001 — not implemented", cls: "danger" },
    2147942402: { text: "0x80070002 — file not found", cls: "danger" },
    2147942405: { text: "0x80070005 — access denied", cls: "danger" },
    2147943645: { text: "0x8007045D — no logged-on user / service error", cls: "warn" },
    2147946720: { text: "0x800710E0 — operator refused / not logged on", cls: "warn" },
    3221225786: { text: "0xC000013A — terminated (Ctrl+C)", cls: "warn" },
    4294967295: { text: "0xFFFFFFFF — still running or stopped", cls: "warn" },
  };

  function taskResultLabel(code) {
    const c = num(code);
    if (c === null) return { text: null, cls: "" };
    const known = TASK_CODES[c];
    if (known) return known;
    const hex = (c >>> 0).toString(16).toUpperCase();
    return { text: `Exit ${c} (0x${hex})`, cls: "warn" };
  }

  // GATE_BLOCKED is the DESIGNED steady state for a log-only probe awaiting
  // owner sign-off — neutral, not an alarm.
  //
  // STARVED is NOT self-explanatory: it covers both a BROKEN probe (it cannot
  // emit entries at all) and an IDLE-BY-MARKET probe (it works; nothing in its
  // frozen universe qualified). This code no longer asserts the alarming one
  // for both. It downgrades a STARVED lane to the informational
  // "IDLE — NO QUALIFYING EVENTS" only on positive evidence from
  // /api/funnel -> lane_skips: every proposal in the window was an
  // out-of-scope skip (see _SCOPE_SKIP_REASONS in mission_control/state.py).
  // Zero proposals, a mixed/ambiguous skip distribution, an unreadable
  // warehouse, or a lane with no decision column all stay in the ALARM tier —
  // absence of evidence is never read as evidence of an empty market.
  const SCOPE_IDLE_TEXT = "IDLE — NO QUALIFYING EVENTS";

  // Positive-evidence test. Returns null when the lane cannot be triaged.
  function laneSkipInfo(laneName) {
    const f = state.funnel || {};
    const ls = f.lane_skips;
    if (!ls || ls.available !== true || !ls.lanes) return null;
    const row = ls.lanes[laneName];
    if (!row || row.readable !== true) return null;
    return Object.assign({ window_days: ls.window_days }, row);
  }

  function isScopeIdle(laneName) {
    const info = laneSkipInfo(laneName);
    return !!(info && info.scope_only === true && num(info.total) > 0);
  }

  // Dominant skip reason, shown in BOTH tiers so the owner can see WHY.
  function dominantSkipPhrase(laneName) {
    const info = laneSkipInfo(laneName);
    if (!info || !info.dominant || !num(info.total)) return "";
    return `${info.dominant_count}/${info.total} proposals in ${info.window_days}d were ${info.dominant}`;
  }

  function laneBadge(laneState, laneName) {
    const s = String(laneState || "UNKNOWN").toUpperCase();
    // Reconstructed from badge text by the legend, so match it before STARV.
    if (s.includes("NO QUALIFYING"))
      return { text: SCOPE_IDLE_TEXT, cls: "neutral", bar: "idle",
        why: "The probe ran and declined every event as outside its frozen universe. Working as designed — no qualifying events occurred." };
    if (s.includes("PROMOT") || s.includes("READY") || s.includes("PASS"))
      return { text: s, cls: "ok", bar: "passed", why: "Gate passed — awaiting owner sign-off." };
    if (s.includes("GATE_BLOCK") || s.includes("BLOCK"))
      return { text: s, cls: "neutral", bar: "blocked", why: "Floor reached, frozen gate refused promotion. Expected outcome, not a fault." };
    if (s.includes("STARV")) {
      if (laneName && isScopeIdle(laneName))
        return laneBadge(SCOPE_IDLE_TEXT);
      const why = laneName && dominantSkipPhrase(laneName)
        ? `No resolved outcomes. Skips are not all out-of-scope (${dominantSkipPhrase(laneName)}) — this may be a broken probe, not an empty market.`
        : "No resolved outcomes, and there is no evidence the lane is merely idle — treat as a possible fault until the skip reasons say otherwise.";
      return { text: s, cls: "danger", bar: "starved", why };
    }
    if (s.includes("ACCRU"))
      return { text: s, cls: "info", bar: "accruing", why: "Collecting resolved outcomes toward the floor." };
    if (s.includes("IDLE"))
      return { text: s, cls: "warn", bar: "idle", why: "No recent proposals from this probe." };
    return { text: s, cls: "", bar: "", why: "" };
  }

  /* ---------- charts ---------- */

  // Win-rate band strip: the 59–67% target band is drawn as a visible hatched
  // region; the marker is the measured WR; the whisker is the 95% CI.
  function wrStrip(opts) {
    const wr = num(opts.wr);
    const lo = num(opts.bandLow) ?? 0.59;
    const hi = num(opts.bandHigh) ?? 0.67;
    const ci = Array.isArray(opts.ci) ? opts.ci.map(num) : [null, null];
    const pctX = (v) => Math.max(0, Math.min(100, v * 100));
    const inBand = wr !== null && wr >= lo && wr <= hi;
    const marker = wr === null ? "" :
      `<div class="wr-marker ${inBand ? "in-band" : ""}" style="left:${pctX(wr).toFixed(2)}%" title="measured WR ${esc(fmtPct(wr))}"></div>`;
    const ciBar = ci[0] !== null && ci[1] !== null
      ? `<div class="wr-ci" style="left:${pctX(ci[0]).toFixed(2)}%;width:${(pctX(ci[1]) - pctX(ci[0])).toFixed(2)}%" title="95% CI ${esc(fmtPct(ci[0]))}–${esc(fmtPct(ci[1]))}"></div>`
      : "";
    const title = opts.label
      ? `<div class="wr-title">${esc(opts.label)} — <b>${wr === null ? "no resolved outcomes" : fmtPct(wr)}</b>${inBand ? " (in band)" : wr === null ? "" : wr < lo ? " (below band)" : " (above band)"}</div>`
      : "";
    return `<div class="wrstrip">
      ${title}
      <div class="wr-track" role="img" aria-label="Win rate ${wr === null ? "unknown" : fmtPct(wr)} against the ${fmtPct(lo, 0)}–${fmtPct(hi, 0)} target band">
        <div class="wr-band" style="left:${pctX(lo)}%;width:${(pctX(hi) - pctX(lo)).toFixed(2)}%" title="target band ${esc(fmtPct(lo, 0))}–${esc(fmtPct(hi, 0))}"></div>
        ${ciBar}${marker}
      </div>
      <div class="wr-scale">
        <span style="left:2%">0</span>
        <span class="band-edge" style="left:${pctX(lo)}%">${(lo * 100).toFixed(0)}</span>
        <span class="band-edge" style="left:${pctX(hi)}%">${(hi * 100).toFixed(0)}</span>
        <span style="left:98%">100</span>
      </div>
    </div>`;
  }

  // Equity curve with axis grid, hover crosshair + tooltip, keyboard stepping.
  let equityData = null;
  function renderEquity(rows) {
    const svg = $("equity-chart");
    const ordered = rows
      .filter((r) => num(r.close_time) !== null && num(r.pnl) !== null)
      .sort((a, b) => num(a.close_time) - num(b.close_time));
    if (ordered.length < 2) {
      svg.setAttribute("viewBox", "0 0 800 220");
      svg.innerHTML = `<text x="400" y="110" text-anchor="middle" fill="#64748b" font-size="13">Not enough closed trades in this window to draw a curve</text>`;
      $("equity-caption").textContent = "";
      equityData = null;
      return;
    }
    const W = Math.max(360, Math.round(svg.getBoundingClientRect().width) || 800);
    const H = 220;
    const padL = 56, padR = 14, padT = 14, padB = 22;
    let cum = 0;
    const pts = ordered.map((r) => {
      cum += num(r.pnl);
      return { t: num(r.close_time), sym: r.symbol, pnl: num(r.pnl), cum };
    });
    const min = Math.min(0, ...pts.map((p) => p.cum));
    const max = Math.max(0, ...pts.map((p) => p.cum));
    const span = max - min || 1;
    const x = (i) => padL + (i / (pts.length - 1)) * (W - padL - padR);
    const y = (v) => padT + (1 - (v - min) / span) * (H - padT - padB);
    const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(" ");
    const endNeg = cum < 0;
    const col = endNeg ? "#e25749" : "#7fd8a4";

    // grid: 4 ticks
    let grid = "";
    for (let g = 0; g <= 3; g++) {
      const v = min + (span * g) / 3;
      const gy = y(v).toFixed(1);
      grid += `<line x1="${padL}" y1="${gy}" x2="${W - padR}" y2="${gy}" stroke="#22303d" stroke-width="1"/>` +
        `<text x="${padL - 6}" y="${gy}" text-anchor="end" dominant-baseline="middle" fill="#64748b" font-size="10" font-family="Consolas,monospace">${esc(fmtMoney(v))}</text>`;
    }
    const zeroY = y(0).toFixed(1);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML =
      grid +
      `<line x1="${padL}" y1="${zeroY}" x2="${W - padR}" y2="${zeroY}" stroke="#9dacbc" stroke-width="1" stroke-dasharray="4 4" opacity="0.5"/>` +
      `<polygon points="${x(0)},${zeroY} ${line} ${x(pts.length - 1)},${zeroY}" fill="${col}" opacity="0.10"/>` +
      `<polyline points="${line}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round"/>` +
      `<circle cx="${x(pts.length - 1)}" cy="${y(cum)}" r="3.5" fill="${col}"/>` +
      `<text x="${W - padR}" y="${Math.max(12, y(cum) - 8)}" text-anchor="end" fill="${col}" font-size="11" font-weight="600" font-family="Consolas,monospace">${esc(fmtSignedMoney(cum))}</text>` +
      `<g id="eq-cross" style="display:none"><line id="eq-vline" y1="${padT}" y2="${H - padB}" stroke="#53c7de" stroke-width="1" opacity="0.6"/><circle id="eq-dot" r="4" fill="#53c7de" stroke="#0a0f14" stroke-width="2"/></g>` +
      `<rect id="eq-hit" x="${padL}" y="0" width="${W - padL - padR}" height="${H}" fill="transparent"/>`;
    equityData = { pts, x, y, padL, W, padR };
    $("equity-caption").textContent =
      `Cumulative net PnL (after fees) across ${pts.length} closed trades in this window — ending at ${fmtMoney(cum)}. ` +
      `Not an account equity curve: it excludes open positions and any trades older than the 500-row cap.`;

    let focusIdx = null;
    const showAt = (i, clientX, clientY) => {
      const p = equityData.pts[i];
      const cross = svg.querySelector("#eq-cross");
      const vline = svg.querySelector("#eq-vline");
      const dot = svg.querySelector("#eq-dot");
      cross.style.display = "";
      vline.setAttribute("x1", x(i)); vline.setAttribute("x2", x(i));
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(p.cum));
      showTip(
        `<b>${esc(p.sym || "trade")}</b> · ${esc(fmtEpoch(p.t) || "")}<br/>` +
        `trade <span class="${p.pnl < 0 ? "neg" : "pos"}">${esc(fmtSignedMoney(p.pnl, 4))}</span>` +
        `<br/><span class="t-sub">cumulative ${esc(fmtSignedMoney(p.cum, 2))}</span>`,
        clientX, clientY
      );
    };
    const hide = () => {
      const cross = svg.querySelector("#eq-cross");
      if (cross) cross.style.display = "none";
      hideTip();
    };
    svg.onmousemove = (ev) => {
      if (!equityData) return;
      const rect = svg.getBoundingClientRect();
      const sx = ((ev.clientX - rect.left) / rect.width) * W;
      const frac = (sx - padL) / (W - padL - padR);
      const i = Math.max(0, Math.min(pts.length - 1, Math.round(frac * (pts.length - 1))));
      showAt(i, ev.clientX, ev.clientY);
    };
    svg.onmouseleave = hide;
    svg.onkeydown = (ev) => {
      if (!equityData) return;
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
      ev.preventDefault();
      focusIdx = focusIdx === null ? pts.length - 1 : focusIdx + (ev.key === "ArrowRight" ? 1 : -1);
      focusIdx = Math.max(0, Math.min(pts.length - 1, focusIdx));
      const rect = svg.getBoundingClientRect();
      const cx = rect.left + (x(focusIdx) / W) * rect.width;
      showAt(focusIdx, cx, rect.top + 40);
    };
    svg.onblur = () => { focusIdx = null; hide(); };
  }

  // Position bracket track: SL → entry → TP, with mark dot only when a live
  // mark actually exists in the state file — never guessed.
  function bracketTrack(p) {
    const entry = num(p.entry_price), sl = num(p.stop_loss), tp = num(p.take_profit);
    if (entry === null || sl === null || tp === null || sl === tp) return "";
    const lo = Math.min(sl, tp), hi = Math.max(sl, tp);
    const posOf = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
    const mark = num(p.current_price ?? p.mark_price ?? p.last_price);
    const leftIsSL = sl < tp;
    const markDot = mark !== null
      ? `<div class="bk-mark" style="left:${posOf(mark).toFixed(2)}%" title="mark ${esc(fmtPrice(mark))}"></div>` : "";
    return `<div class="bracket">
      <div class="bk-track">
        <div class="bk-rail" style="${leftIsSL ? "" : "transform:scaleX(-1)"}"></div>
        <div class="bk-node sl" style="left:${posOf(sl).toFixed(2)}%" title="stop loss ${esc(fmtPrice(sl))}"></div>
        <div class="bk-node entry" style="left:${posOf(entry).toFixed(2)}%" title="entry ${esc(fmtPrice(entry))}"></div>
        <div class="bk-node tp" style="left:${posOf(tp).toFixed(2)}%" title="take profit ${esc(fmtPrice(tp))}"></div>
        ${markDot}
      </div>
      <div class="bk-labels">
        <div class="lab"><span>${leftIsSL ? "STOP" : "TARGET"}</span><b>${esc(fmtPrice(leftIsSL ? sl : tp))}</b></div>
        <div class="lab mid"><span>ENTRY</span><b>${esc(fmtPrice(entry))}</b></div>
        <div class="lab end"><span>${leftIsSL ? "TARGET" : "STOP"}</span><b>${esc(fmtPrice(leftIsSL ? tp : sl))}</b></div>
      </div>
    </div>`;
  }

  function heroCard(p, extraCount) {
    const side = sideLabel(p.side);
    const entry = num(p.entry_price), sl = num(p.stop_loss), tp = num(p.take_profit), size = num(p.size);
    const mark = num(p.current_price ?? p.mark_price ?? p.last_price);
    const ageS = num(p.open_time) !== null ? Date.now() / 1000 - num(p.open_time) : null;
    let rr = null;
    if (entry !== null && sl !== null && tp !== null && Math.abs(entry - sl) > 0)
      rr = `${(Math.abs(tp - entry) / Math.abs(entry - sl)).toFixed(2)}:1`;
    let unreal = null, rMult = null;
    if (mark !== null && entry !== null && size !== null) {
      const dir = side.pill === "short" ? -1 : 1;
      unreal = (mark - entry) * size * dir;
      if (sl !== null && Math.abs(entry - sl) > 0) rMult = ((mark - entry) * dir) / Math.abs(entry - sl);
    }
    const facts = [
      `<span>notional <b data-mk="hero.notional">${cell(entry !== null && size !== null ? fmtMoney(entry * size) : null)}</b></span>`,
      `<span>size <b>${cell(fmtNum(size, 8))}</b></span>`,
      `<span>lev <b>${cell(fmtNum(p.leverage, 2))}</b></span>`,
      `<span>R:R <b>${cell(rr)}</b></span>`,
      `<span>age <b>${cell(fmtAge(ageS))}</b></span>`,
      unreal !== null
        ? `<span>unrealized <b class="${unreal < 0 ? "neg" : "pos"}" data-mk="hero.unreal" data-raw="${unreal}">${esc(fmtSignedMoney(unreal, 4))}</b></span>`
        : `<span>unrealized <span class="na" title="No live mark price in the state file — not guessed">n/a (no mark)</span></span>`,
      rMult !== null ? `<span>R <b data-mk="hero.r">${esc(rMult.toFixed(2))}R</b></span>` : "",
      `<span>${esc(p.exchange || "")} · ${esc(p.strategy || "")}</span>`,
    ].join("");
    const more = extraCount > 0 ? `<span class="badge info">+${extraCount} more open — see table</span>` : "";
    return `<div class="hero-card">
      <div class="hero-head">
        <span class="hero-sym">${esc(p.symbol || "?")}</span>
        <span class="side-pill ${side.pill}">${cell(side.text)}</span>
        ${more}
      </div>
      ${bracketTrack(p)}
      <div class="hero-facts">${facts}</div>
    </div>`;
  }

  function flatCard() {
    return `<div class="hero-card flat">
      <span class="crosshair">+</span>
      <div><b>Flat — no open position.</b> The bot is holding no directional exposure right now.
      That is a real answer from data/positions.json, not missing data. Entry candidates and the
      reasons they were skipped live under “Why no trades”.</div>
    </div>`;
  }

  /* ---------- header ---------- */

  function renderErrors() {
    const keys = Object.keys(state.errors);
    const host = $("feed-errors");
    if (!keys.length) { host.hidden = true; host.innerHTML = ""; return; }
    host.hidden = false;
    host.innerHTML = keys
      .map((k) => `<div class="alert danger"><strong>${esc(k)}</strong> failed to load — ${esc(state.errors[k])}. Values shown for this panel may be stale.</div>`)
      .join("");
  }

  function renderHeader() {
    const s = state.status || {};
    const hb = s.heartbeat || {};
    const age = num(s.heartbeat_age_seconds);

    // heartbeat pulse: rhythm class from real age
    const pulse = $("hb-pulse");
    const pCls = age === null ? "warn" : age > 900 ? "danger" : age > 300 ? "warn" : "ok";
    pulse.className = `pulse ${pCls}`;
    pulse.title = age === null ? "Heartbeat age unknown" : `Bot heartbeat ${fmtAge(age)} ago (pid ${hb.pid ?? "?"})`;

    const tag = $("mode-tag");
    const mode = s.mode || "?";
    tag.textContent = `${mode}${s.paper_profile ? " · " + s.paper_profile : ""}`;
    tag.className = `mode-tag ${mode === "PAPER" ? "paper" : mode === "CONTROLLED_LIVE" ? "live" : mode === "OBSERVATION" ? "observation" : ""}`;

    const parts = [
      `pid <b>${cell(hb.pid)}</b>`,
      `uptime <b data-mk="sl.uptime">${cell(fmtUptime(hb.uptime_seconds))}</b>`,
      `cycles <b data-mk="sl.cycles" data-raw="${esc(hb.cycle_count ?? "")}">${cell(fmtNum(hb.cycle_count, 0))}</b>`,
      `mem <b>${cell(fmtNum(hb.memory_mb, 0))}MB</b>`,
      `heartbeat <b data-mk="sl.hb">${age === null ? "?" : esc(fmtAge(age))} ago</b>`,
      `positions file <b>${cell(fmtAge(state.positions && state.positions.file && state.positions.file.age_seconds))}</b>`,
      `funnel <b>${cell(fmtAge(state.funnel && state.funnel.file && state.funnel.file.age_seconds))}</b>`,
      `goals <b>${cell(fmtAge(state.goals && state.goals.file && state.goals.file.age_seconds))}</b>`,
    ];
    $("statusline").innerHTML = parts.join(" · ");
  }

  function renderChips() {
    const s = state.status || {};
    const hb = s.heartbeat || {};
    const age = num(s.heartbeat_age_seconds);
    const ageCls = age === null ? "warn" : age > 900 ? "danger" : age > 300 ? "warn" : "ok";
    const sup = s.supervisor || {};
    const aliveCls = sup.alive === true ? ((sup.warnings || []).length ? "warn" : "ok") : sup.alive === false ? "danger" : "warn";
    const supValue = sup.alive === true ? `Up (${sup.supervisor_count ?? "?"} sup / ${sup.worker_count ?? "?"} main)` : sup.alive === false ? "Down" : "Unknown";

    const chips = [
      { label: "Mode", value: s.mode || null, cls: s.mode_divergent ? "warn" : "info",
        title: `Source: ${s.mode_source || "unknown"}${s.mode_divergent ? ` — .env says ${s.env_mode}, pending a supervisor restart` : ""}` },
      { label: "Profile", value: s.paper_profile || null, cls: s.profile_divergent ? "warn" : "info",
        title: `Source: ${s.profile_source || "unknown"}${s.profile_divergent ? ` — .env says ${s.env_paper_profile}, pending a supervisor restart` : ""}` },
      { label: "Heartbeat", value: age === null ? null : `${fmtAge(age)} ago`, cls: ageCls },
      { label: "Halt", value: s.is_halted ? "HALTED" : "Clear", cls: s.is_halted ? "danger" : "ok" },
      { label: "New entries", value: s.kill_switch ? "Paused" : "Allowed", cls: s.kill_switch ? "warn" : "ok" },
      { label: "Processes", value: supValue, cls: aliveCls, title: (sup.processes || []).map((p) => `${p.pid} ${p.role}`).join(", ") },
      { label: "Open positions", value: hb.open_positions ?? null, cls: "info", raw: hb.open_positions },
      { label: "Daily PnL", value: fmtSignedMoney(hb.daily_pnl), raw: num(hb.daily_pnl),
        cls: num(hb.daily_pnl) < 0 ? "danger" : num(hb.daily_pnl) > 0 ? "ok" : "info" },
    ];

    $("status-chips").innerHTML = chips
      .map((c) =>
        `<div class="chip ${c.cls}"${c.title ? ` title="${esc(c.title)}"` : ""}><span class="label">${esc(c.label)}</span><span class="value" data-mk="chip.${esc(c.label)}"${c.raw !== undefined && c.raw !== null ? ` data-raw="${esc(c.raw)}"` : ""}>${c.value === null || c.value === undefined ? NA : esc(c.value)}</span></div>`)
      .join("");
  }

  function renderGuidance() {
    const el = $("guidance");
    const s = state.status || {};
    const sup = s.supervisor || {};
    const age = num(s.heartbeat_age_seconds);
    const set = (cls, text) => { el.className = `guidance ${cls}`; el.textContent = text; };

    if (!state.status) return set("warn", "Waiting for the first successful poll…");
    if (s.is_halted)
      return set("danger", "Entries are HALTED by an incident latch. Review it on the Risk tab; “Clear incident” in the Danger rail archives and removes the latch (audited), then restart the supervisor.");
    if (s.mode_divergent || s.profile_divergent)
      return set("warn",
        `The running bot reports ${s.mode || "?"} / ${s.paper_profile || "?"}, but .env says ${s.env_mode || "?"} / ${s.env_paper_profile || "?"}. A long-running supervisor never re-reads .env — the edit is NOT in effect until it is restarted.`);
    if (sup.alive === false)
      return set("warn", "No launcher_supervisor or main.py process is running. The bot is not trading.");
    if (sup.alive === null)
      return set("warn", "Process liveness is UNKNOWN (psutil is not installed). This is not the same as 'the bot is down'.");
    if ((sup.warnings || []).length) return set("warn", sup.warnings[0]);
    if (age !== null && age > 900)
      return set("warn", `Heartbeat is ${fmtAge(age)} old (expected under ~15m). The process may be hung.`);
    if (s.kill_switch) return set("warn", "Kill switch is ON — new entries are paused. Open positions keep monitoring.");
    return set("ok",
      `Healthy · ${s.mode || "?"} · profile ${s.paper_profile || "?"} · heartbeat ${fmtAge(age) || "?"} ago. Shadow probes are log-only and cannot place orders.`);
  }

  /* ---------- overview ---------- */

  function matureNegativeLanes() {
    const goals = state.goals || {};
    const lanes = Array.isArray(goals.lanes) ? goals.lanes : [];
    return lanes.filter((g) =>
      g && g.available !== false && g.target_wr_low !== undefined &&
      num(g.closed_outcomes) > 0 && num(g.net_after_cost_pnl) !== null && num(g.net_after_cost_pnl) < 0);
  }

  function renderEconBanner() {
    const host = $("econ-banner");
    const negs = matureNegativeLanes();
    if (!negs.length) { host.innerHTML = ""; return; }
    const worst = negs.slice().sort((a, b) => num(a.net_after_cost_pnl) - num(b.net_after_cost_pnl))[0];
    const wr = num(worst.win_rate);
    host.innerHTML = `<div class="econ-banner">
      <span class="tag">NEGATIVE AFTER-COST ECONOMICS</span>
      ${esc(worst.lane || "lane")}: net <b class="neg mono">${esc(fmtMoney(worst.net_after_cost_pnl))}</b>
      over ${esc(fmtNum(worst.closed_outcomes, 0))} outcomes${wr === null ? "" : `, WR ${esc(fmtPct(wr))} vs the ${esc(fmtPct(worst.target_wr_low, 0))}–${esc(fmtPct(worst.target_wr_high, 0))} target band`}.
      This is the measured truth of the paper cohort — band geometry is not edge, and this banner stays up while expectancy is negative.
    </div>`;
  }

  function renderOverview() {
    const s = state.status || {};
    const hb = s.heartbeat || {};
    const risk = state.risk || {};
    const rs = risk.risk_state || {};
    const funnel = state.funnel || {};
    const lanes = Array.isArray(funnel.lanes) ? funnel.lanes : [];
    const wallet = (state.positions && state.positions.wallet_summary) || {};

    // hero position
    const open = (state.positions && Array.isArray(state.positions.open)) ? state.positions.open : [];
    const heroHtml = open.length ? heroCard(open[0], open.length - 1) : flatCard();
    $("hero-position").innerHTML = heroHtml;
    const posHero = $("positions-hero");
    if (posHero) posHero.innerHTML = open.length ? heroHtml : "";

    renderEconBanner();

    const byState = {};
    lanes.forEach((l) => {
      const k = String(l.state || "UNKNOWN").toUpperCase();
      byState[k] = (byState[k] || 0) + 1;
    });
    const laneBreakdown = Object.keys(byState).sort().map((k) => `${k.toLowerCase()} ${byState[k]}`).join(" · ");
    const dailyRaw = num(rs.daily_pnl ?? hb.daily_pnl);

    $("overview-metrics").innerHTML = [
      // The total is a balance, not a return — colour the delta, not the total.
      metricCard("Wallet total", fmtMoney(wallet.total), {
        mk: "ov.wallet", raw: wallet.total,
        sub: wallet.delta_vs_seed === null || wallet.delta_vs_seed === undefined ? "" : `${fmtSignedMoney(wallet.delta_vs_seed)} vs seed`,
        title: wallet.basis || "" }),
      metricCard("Daily PnL", fmtSignedMoney(dailyRaw), {
        mk: "ov.daily", raw: dailyRaw,
        cls: dailyRaw !== null && dailyRaw < 0 ? "neg" : dailyRaw !== null && dailyRaw > 0 ? "pos" : "" }),
      metricCard("Trades today", fmtNum(rs.trades_today, 0), { mk: "ov.trades", raw: rs.trades_today }),
      metricCard("Max drawdown",
        risk.max_drawdown_percent === null || risk.max_drawdown_percent === undefined ? null : `${fmtNum(risk.max_drawdown_percent, 2)}%`, {
        mk: "ov.dd", raw: risk.max_drawdown_percent,
        title: "risk_state.max_drawdown_pct is stored as a fraction; shown here converted to percent." }),
      metricCard("Open positions", hb.open_positions ?? null, { mk: "ov.open", raw: hb.open_positions }),
      metricCard("Cycles", fmtNum(hb.cycle_count, 0), { mk: "ov.cycles", raw: hb.cycle_count }),
      metricCard("Probe lanes", lanes.length ? String(lanes.length) : null, { sub: laneBreakdown }),
      metricCard("Entry policy", hb.entry_policy || null),
      metricCard("Exchanges", (hb.active_exchanges || []).join(", ") || null),
    ].join("");

    $("overview-explain").innerHTML = [
      "<li><strong>Mode</strong> and <strong>Profile</strong> are read from the bot's heartbeat — what the running process actually reports, not what <code>.env</code> asks for.</li>",
      "<li><strong>Heartbeat</strong> is the pulsing dot in the header. Over ~15 minutes old means the process may be hung.</li>",
      "<li><strong>Shadow probes</strong> are log-only experiments. <code>GATE_BLOCKED</code> means the frozen gate correctly refused promotion — the expected state. <code>STARVED</code> is an alarm only when the lane's skip reasons do not prove it is merely idle; a lane whose every proposal was declined as outside its frozen universe reads <code>IDLE — NO QUALIFYING EVENTS</code> and sits in Status, not here.</li>",
      "<li><strong>Live gates</strong> block real money until the checklist is signed on disk, by hand, by you. That is a status line, not a fault.</li>",
      "<li><strong>Operations rail</strong> is the only mutating surface: every action needs a note, destructive ones an exact phrase, and all of it is audited.</li>",
    ].join("");

    const alerts = [];
    const sup = s.supervisor || {};
    if (s.is_halted) alerts.push({ cls: "danger", text: "Incident latch is active — new entries are blocked until it is cleared (Danger rail → Clear incident)." });
    if (s.mode_divergent)
      alerts.push({ cls: "warn", text: `Mode divergence: running=${s.mode}, .env=${s.env_mode}. The .env value applies only after a supervisor restart.` });
    if (s.profile_divergent)
      alerts.push({ cls: "warn", text: `Profile divergence: running=${s.paper_profile}, .env=${s.env_paper_profile}. Not in effect until restart.` });
    if (s.open_positions_divergent)
      alerts.push({ cls: "danger",
        text: `Open-position count disagrees: the heartbeat reports ${s.open_positions_heartbeat}, positions.json holds ${s.open_positions_file}. One of them is wrong — check for a ghost or unreconciled position.` });
    (sup.warnings || []).forEach((w) => alerts.push({ cls: "warn", text: w }));
    if (s.kill_switch) alerts.push({ cls: "warn", text: "Kill switch ON — new entries paused by operator." });
    if (num(s.heartbeat_age_seconds) !== null && num(s.heartbeat_age_seconds) > 900)
      alerts.push({ cls: "danger", text: `Heartbeat stale (${fmtAge(s.heartbeat_age_seconds)}).` });
    // A STARVED lane is an alarm unless its own skip reasons prove it is idle
    // by market. Scope-idle lanes move to the Status tier below instead of
    // being dropped — the owner still sees them, just not as a fault.
    const notes = [];
    lanes.filter((l) => String(l.state || "").toUpperCase().includes("STARV"))
      .forEach((l) => {
        const why = dominantSkipPhrase(l.lane);
        if (isScopeIdle(l.lane)) {
          notes.push({ cls: "info",
            text: `Probe lane "${l.lane}" is IDLE — NO QUALIFYING EVENTS${why ? ` (${why})` : ""}. The probe ran and declined every event as outside its frozen universe. Nothing to fix.` });
          return;
        }
        alerts.push({ cls: "warn",
          text: `Probe lane "${l.lane}" is STARVED — no resolved outcomes, and its skips do not prove it is merely idle${why ? ` (${why})` : " (skip reasons unavailable)"}. Check whether the probe can emit entries at all.` });
      });
    if (!alerts.length) alerts.push({ cls: "ok", text: "No urgent issues detected in the bot's state files." });

    // The unsigned CONTROLLED_LIVE checklist is a correct, protective,
    // owner-only state — the real-money path being fail-closed is the design,
    // not a fault. It lives in the Status tier so "Needs attention" keeps
    // meaning "something is wrong"; surfacing it beside genuine faults trains
    // the owner to ignore the panel. Presentation only: nothing here reads or
    // writes core/live_gate.py or the checklist file.
    if (state.gates && state.gates.checklist_signed === false)
      notes.push({ cls: "info",
        text: `Live checklist unsigned${state.gates.unchecked_count ? ` (${state.gates.unchecked_count} item(s) left)` : ""} — expected. The real-money path stays fail-closed until docs/CONTROLLED_LIVE_CHECKLIST.md is signed by hand by you, the human owner. No agent may ever sign it, and this console never writes a signature.` });
    if (!notes.length) notes.push({ cls: "info", text: "No expected-state notices right now." });

    $("overview-alerts").innerHTML = alerts.map((a) => `<div class="alert ${a.cls}">${esc(a.text)}</div>`).join("");
    $("overview-status").innerHTML = notes.map((a) => `<div class="alert ${a.cls}">${esc(a.text)}</div>`).join("");
  }

  /* ---------- positions ---------- */

  function renderPositions() {
    const data = state.positions;
    const tbody = $("positions-table").querySelector("tbody");
    if (!data) {
      $("positions-summary").innerHTML = NA;
      tbody.innerHTML = `<tr><td colspan="12" class="empty">Positions feed unavailable.</td></tr>`;
      return;
    }
    const capped = data.closed_is_capped
      ? `last ${data.closed_ring_cap} closed tracked (rolling cap in positions.json — not a lifetime total)`
      : `${data.closed_count} closed tracked`;
    $("positions-summary").innerHTML = `${esc(data.open_count)} open · ${esc(capped)}`;

    const open = Array.isArray(data.open) ? data.open : [];
    if (!open.length) {
      tbody.innerHTML = `<tr><td colspan="12" class="empty">No open positions. The bot is flat — this is a real answer, not missing data.</td></tr>`;
    } else {
      tbody.innerHTML = open.map((p) => {
        const side = sideLabel(p.side);
        const entry = num(p.entry_price), sl = num(p.stop_loss), tp = num(p.take_profit), size = num(p.size);
        let rr = null;
        if (entry !== null && sl !== null && tp !== null && Math.abs(entry - sl) > 0)
          rr = `${(Math.abs(tp - entry) / Math.abs(entry - sl)).toFixed(2)} : 1`;
        const notional = entry !== null && size !== null ? entry * size : null;
        const ageS = num(p.open_time) !== null ? Date.now() / 1000 - num(p.open_time) : null;
        return `<tr>
          <td class="mono">${cell(p.symbol)}</td>
          <td class="${side.cls}">${cell(side.text)}</td>
          <td>${cell(p.exchange)}</td>
          <td class="num mono">${cell(fmtPrice(entry))}</td>
          <td class="num mono">${cell(fmtPrice(sl))}</td>
          <td class="num mono">${cell(fmtPrice(tp))}</td>
          <td class="num mono">${cell(rr)}</td>
          <td class="num mono">${cell(fmtNum(size, 8))}</td>
          <td class="num mono">${cell(fmtMoney(notional))}</td>
          <td class="num mono">${cell(fmtNum(p.leverage, 2))}</td>
          <td class="num mono">${cell(fmtAge(ageS))}</td>
          <td>${cell(p.strategy)}</td>
        </tr>`;
      }).join("");
    }

    const w = data.wallet_summary || {};
    $("wallet-metrics").innerHTML = [
      metricCard("Total across venues", fmtMoney(w.total), { mk: "w.total", raw: w.total }),
      metricCard("Seed total", fmtMoney(w.seed_total), { sub: w.venues ? `${fmtMoney(w.start_per_venue)} × ${w.venues} venues` : "" }),
      metricCard("Δ vs seed", fmtSignedMoney(w.delta_vs_seed), {
        mk: "w.delta", raw: w.delta_vs_seed,
        cls: num(w.delta_vs_seed) < 0 ? "neg" : num(w.delta_vs_seed) > 0 ? "pos" : "" }),
    ].join("");

    // per-venue delta bars vs the per-venue seed
    const balances = w.balances || {};
    const venues = Object.keys(balances);
    const seed = num(w.start_per_venue);
    const host = $("wallet-bars");
    if (!venues.length) {
      host.innerHTML = `<p class="hint"><span class="na">No wallet balances in data/virtual_wallet.json.</span></p>`;
    } else {
      const deltas = venues.map((k) => {
        const bal = num(balances[k]);
        return { venue: k, bal, delta: bal !== null && seed !== null ? bal - seed : null };
      });
      const maxAbs = Math.max(1e-9, ...deltas.map((d) => (d.delta === null ? 0 : Math.abs(d.delta))));
      host.innerHTML = deltas.map((d) => {
        const frac = d.delta === null ? 0 : Math.min(1, Math.abs(d.delta) / maxAbs) * 48;
        const dir = d.delta !== null && d.delta < 0;
        const bar = d.delta === null ? "" :
          `<div class="wbar-fill ${dir ? "neg" : "pos"}" style="${dir ? `right:50%;width:${frac.toFixed(1)}%` : `left:50%;width:${frac.toFixed(1)}%`}"></div>`;
        return `<div class="wbar" title="${esc(d.venue)}: balance ${esc(fmtMoney(d.bal) || "no data")}, seed ${esc(fmtMoney(seed) || "?")}">
          <span class="venue">${esc(d.venue)}</span>
          <div class="wbar-track"><div class="wbar-zero" title="seed ${esc(fmtMoney(seed) || "")}"></div>${bar}</div>
          <span class="figs" data-mk="w.${esc(d.venue)}" data-raw="${esc(d.bal ?? "")}">${cell(fmtMoney(d.bal))}<span class="delta ${num(d.delta) < 0 ? "neg" : num(d.delta) > 0 ? "pos" : "muted"}">${cell(fmtSignedMoney(d.delta))}</span></span>
        </div>`;
      }).join("");
    }

    const rs = (state.risk && state.risk.risk_state) || {};
    $("wallet-basis").innerHTML =
      `${esc(w.basis || "")} · Note: the Risk tab's <code>start_balance</code> (${esc(fmtMoney(rs.start_balance) || "n/a")}) is a ` +
      `<strong>different accounting basis</strong> maintained by risk_manager and will not reconcile with the wallet total.`;
  }

  /* ---------- trade history ---------- */

  function historyRows() {
    const rows = (state.positions && Array.isArray(state.positions.closed) ? state.positions.closed : []).slice();
    const q = state.historyFilter.trim().toLowerCase();
    const rangeH = state.historyRange === "all" ? null : Number(state.historyRange);
    const cutoff = rangeH ? Date.now() / 1000 - rangeH * 3600 : null;
    return rows.filter((r) => {
      if (cutoff !== null) {
        const ct = num(r.close_time);
        if (ct === null || ct < cutoff) return false;
      }
      if (!q) return true;
      return [r.symbol, r.close_reason, r.strategy, r.exchange, r.side]
        .map((v) => String(v || "").toLowerCase())
        .some((v) => v.includes(q));
    });
  }

  function renderHistory() {
    const data = state.positions;
    const tbody = $("history-table").querySelector("tbody");
    if (!data) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty">Positions feed unavailable.</td></tr>`;
      $("history-metrics").innerHTML = "";
      $("history-wr").innerHTML = "";
      return;
    }
    const rows = historyRows();
    const withPnl = rows.map((r) => num(r.pnl)).filter((v) => v !== null);
    const wins = withPnl.filter((v) => v > 0).length;
    const losses = withPnl.filter((v) => v < 0).length;
    const netSum = withPnl.reduce((a, b) => a + b, 0);
    const feeSum = rows.map((r) => num(r.total_fees)).filter((v) => v !== null).reduce((a, b) => a + b, 0);
    const grossWin = withPnl.filter((v) => v > 0).reduce((a, b) => a + b, 0);
    const grossLoss = Math.abs(withPnl.filter((v) => v < 0).reduce((a, b) => a + b, 0));
    const wr = wins + losses ? wins / (wins + losses) : null;

    $("history-metrics").innerHTML = [
      metricCard("Closed trades", String(rows.length), { mk: "h.count", raw: rows.length }),
      metricCard("Wins / losses", withPnl.length ? `${wins} / ${losses}` : null),
      metricCard("Win rate", wr === null ? null : fmtPct(wr), {
        mk: "h.wr", raw: wr, sub: wins + losses ? `${wins + losses} resolved` : "" }),
      metricCard("Net PnL", withPnl.length ? fmtSignedMoney(netSum) : null, {
        mk: "h.net", raw: netSum, cls: netSum < 0 ? "neg" : netSum > 0 ? "pos" : "" }),
      metricCard("Fees paid", rows.length ? fmtMoney(feeSum) : null),
      metricCard("Profit factor", grossLoss > 0 ? (grossWin / grossLoss).toFixed(2) : null, {
        sub: grossLoss > 0 ? "" : "no losing trades in window" }),
    ].join("");

    $("history-wr").innerHTML = wrStrip({
      wr, label: `Win rate in this window vs the 59–67% target band`,
      bandLow: 0.59, bandHigh: 0.67,
    });

    $("history-note").innerHTML = data.closed_is_capped
      ? `Showing ${rows.length} of ${data.closed_count} tracked trades. positions.json keeps only the most recent ${data.closed_ring_cap} — older trades exist in the warehouse but are not exposed here.`
      : `Showing ${rows.length} of ${data.closed_count} tracked trades.`;

    renderEquity(rows);

    const { key, dir } = state.historySort;
    const sorted = rows.slice().sort((a, b) => {
      const av = a[key], bv = b[key];
      const an = num(av), bn = num(bv);
      if (an !== null && bn !== null) return (an - bn) * dir;
      return String(av ?? "").localeCompare(String(bv ?? "")) * dir;
    });

    tbody.innerHTML = sorted.length
      ? sorted.map((r) => {
          const side = sideLabel(r.side);
          const pnl = num(r.pnl);
          const cls = pnl === null ? "" : pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
          const ret = num(r.pnl_pct);
          return `<tr>
            <td class="mono">${cell(fmtEpoch(r.close_time))}</td>
            <td class="mono">${cell(r.symbol)}</td>
            <td class="${side.cls}">${cell(side.text)}</td>
            <td class="num mono">${cell(fmtPrice(r.entry_price))}</td>
            <td class="num mono">${cell(fmtPrice(r.exit_price))}</td>
            <td class="num mono ${cls}">${cell(fmtSignedMoney(pnl, 4))}</td>
            <td class="num mono ${cls}">${cell(ret === null ? null : `${ret.toFixed(2)}%`)}</td>
            <td class="num mono">${cell(fmtMoney(r.total_fees, 4))}</td>
            <td>${cell(r.close_reason)}</td>
            <td>${cell(r.strategy)}</td>
          </tr>`;
        }).join("")
      : `<tr><td colspan="10" class="empty">No closed trades match this window and filter.</td></tr>`;

    document.querySelectorAll("#history-table th[data-sort]").forEach((th) => {
      const arrow = th.querySelector(".arrow");
      if (arrow) arrow.textContent = th.getAttribute("data-sort") === key ? (dir === 1 ? "▲" : "▼") : "";
    });
  }

  /* ---------- risk ---------- */

  function renderRisk() {
    const risk = state.risk;
    if (!risk) {
      $("risk-metrics").innerHTML = `<div class="alert danger">Risk feed unavailable.</div>`;
      return;
    }
    const rs = risk.risk_state || {};
    $("risk-metrics").innerHTML = [
      metricCard("Halted", rs.is_halted || risk.incident_latch_present ? "YES" : "No", {
        cls: rs.is_halted || risk.incident_latch_present ? "neg" : "" }),
      metricCard("Daily PnL", fmtSignedMoney(rs.daily_pnl), {
        mk: "r.daily", raw: rs.daily_pnl,
        cls: num(rs.daily_pnl) < 0 ? "neg" : num(rs.daily_pnl) > 0 ? "pos" : "" }),
      metricCard("Max drawdown",
        risk.max_drawdown_percent === null || risk.max_drawdown_percent === undefined ? null : `${fmtNum(risk.max_drawdown_percent, 2)}%`, {
        sub: risk.max_drawdown_fraction === null || risk.max_drawdown_fraction === undefined ? "" : `stored as fraction ${risk.max_drawdown_fraction}` }),
      metricCard("Peak balance", fmtMoney(rs.peak_balance)),
      metricCard("Start balance", fmtMoney(rs.start_balance), { sub: "risk_manager basis" }),
      metricCard("Trades today", fmtNum(rs.trades_today, 0), { mk: "r.trades", raw: rs.trades_today, sub: rs.trading_day ? `day ${rs.trading_day}` : "" }),
    ].join("");

    // win = filled square, loss = hollow — never color alone (CVD).
    const recent = Array.isArray(rs.recent_results) ? rs.recent_results.slice(-40) : [];
    $("risk-streak").innerHTML = recent.length
      ? recent.map((w) => `<span class="dot ${w ? "win" : "loss"}" title="${w ? "win" : "loss"}"></span>`).join("")
      : `<span class="na">No recent results recorded</span>`;

    $("risk-basis").innerHTML =
      "Balances above are risk_manager's own accounting basis and intentionally differ from the paper wallet on the Positions tab.";

    const kill = !!risk.kill_switch;
    const killEl = $("kill-state");
    killEl.textContent = kill
      ? "ON — new entries paused (data/KILL_SWITCH exists)"
      : "OFF — new entries allowed (data/KILL_SWITCH absent)";
    killEl.className = `state-pill ${kill ? "warn" : "ok"}`;

    const latch = risk.incident_latch || null;
    $("latch-card").innerHTML = risk.incident_latch_present
      ? `<div class="alert danger"><strong>Latch present</strong> — new entries fail closed until it is cleared.<br/>Reason: ${esc(
          (latch && (latch.reason || latch.halt_reason || latch.message)) || "not stated in the latch file")}</div>`
      : `<div class="alert ok">No incident latch. The entry path is not blocked by a latch.</div>`;
  }

  /* ---------- probes + goals ---------- */

  function gateTable(gate) {
    if (!gate || typeof gate !== "object") return "";
    const gates = gate.gates && typeof gate.gates === "object" ? gate.gates : {};
    const keys = Object.keys(gates);
    if (!keys.length) return "";
    const rows = keys.map((k) => {
      const g = gates[k] || {};
      const notComputable = g.computable === false;
      const ok = g.ok === true;
      const cls = notComputable ? "neutral" : ok ? "ok" : "danger";
      const verdict = notComputable ? "not computable" : ok ? "pass" : "FAIL";
      const value = notComputable ? null : num(g.value) === null ? g.value : fmtNum(g.value, 4);
      const thr = num(g.threshold) === null ? g.threshold : fmtNum(g.threshold, 4);
      const note = g.note ? ` title="${esc(g.note)}"` : "";
      return `<tr${note}><td>${esc(k)}</td><td class="num mono">${cell(value)}</td><td class="num mono">${cell(thr)}</td><td class="verdict ${cls}">${esc(verdict)}</td></tr>`;
    }).join("");
    const passed = gate.passed === true;
    return `<div class="gate-block">
      <div class="gate-verdict ${passed ? "ok" : "neutral"}">Frozen gate: ${passed ? "PASSED — awaiting owner sign-off" : "did not pass"}${gate.fail_closed ? " (fail-closed)" : ""}</div>
      <table class="data mini"><thead><tr><th>Gate</th><th class="num">Value</th><th class="num">Threshold</th><th>Verdict</th></tr></thead><tbody>${rows}</tbody></table>
    </div>`;
  }

  function renderProbes() {
    const funnel = state.funnel || {};
    const grid = $("lane-grid");
    $("funnel-age").innerHTML =
      state.errors.funnel || funnel.available === false ? "" : freshnessLine(funnel.file, funnel.generated_utc, 3600);

    if (state.errors.funnel && !state.funnel) {
      grid.innerHTML = `<div class="alert danger">The funnel feed failed to load (${esc(state.errors.funnel)}). This is a transport failure, not an empty funnel — the data on disk is unknown.</div>`;
      $("lane-legend").innerHTML = "";
      renderGoals();
      return;
    }
    if (funnel.available === false) {
      grid.innerHTML = `<div class="alert warn">${esc(funnel.reason || "No funnel snapshot available.")} The hourly <code>TradingBot_PromotionFunnel</code> task writes it — “Run promotion funnel” in the rail regenerates it now.</div>`;
      $("lane-legend").innerHTML = "";
    } else {
      const lanes = Array.isArray(funnel.lanes) ? funnel.lanes : [];
      const floor = num(funnel.resolved_floor);
      if (!lanes.length) {
        grid.innerHTML = `<div class="alert warn">The funnel snapshot contains no lanes.</div>`;
      } else {
        const counts = {};
        lanes.forEach((l) => {
          const b = laneBadge(l.state, l.lane);
          counts[b.text] = (counts[b.text] || 0) + 1;
        });
        $("lane-legend").innerHTML = Object.keys(counts)
          .map((k) => `<span class="badge ${laneBadge(k).cls}" title="${esc(laneBadge(k).why)}">${esc(k)} ${counts[k]}</span>`)
          .join("");

        grid.innerHTML = lanes.map((lane) => {
          const name = String(lane.lane || "lane");
          const resolved = num(lane.resolved);
          const laneFloor = num(lane.resolved_floor) ?? floor;
          const pct = resolved !== null && laneFloor !== null && laneFloor > 0
            ? Math.min(100, Math.round((resolved / laneFloor) * 100)) : null;
          const badge = laneBadge(lane.state, name);
          const detail = lane.detail && typeof lane.detail === "object" ? lane.detail : {};
          const open = state.openLanes.has(name);
          const cal = detail.calendar && typeof detail.calendar === "object" ? detail.calendar : null;
          const skipInfo = laneSkipInfo(name);
          const skipPhrase = dominantSkipPhrase(name);
          // Both skip rows are shown ONLY where the classification is actually
          // being made — a STARVED lane (a scope-idle lane keeps state STARVED;
          // only its presentation is downgraded). Elsewhere the live 30d probe
          // count would sit next to the hourly snapshot's own proposal count and
          // the two legitimately disagree (unlock_short: snapshot 0 vs live 416,
          // different counting rules), which would publish a contradiction.
          const laneIsStarved = String(lane.state || "").toUpperCase().includes("STARV");
          const floorTick = laneFloor !== null && laneFloor > 0 && pct !== null && pct < 100
            ? `<span class="floor-tick" style="left:100%"></span>` : "";
          return `<div class="lane ${open ? "open" : ""} ${badge.bar === "starved" ? "starved" : ""}" data-lane="${esc(name)}" role="button" tabindex="0" aria-expanded="${open}">
            <div class="lane-top">
              <span class="lane-name">${esc(name)}</span>
              <span class="badge ${badge.cls}" title="${esc(badge.why)}">${esc(badge.text)}</span>
            </div>
            <div class="lanebar ${badge.bar}" title="${badge.bar === "starved" || badge.text === SCOPE_IDLE_TEXT ? esc(badge.why) : pct === null ? "progress unknown" : pct + "% of the " + laneFloor + "-resolved floor"}">
              <span style="width:${pct === null ? 0 : pct}%"></span>${floorTick}
            </div>
            <div class="lane-meta">
              <span data-mk="lane.${esc(name)}.prog">${cell(lane.floor_progress || (resolved !== null && laneFloor !== null ? `${resolved}/${laneFloor}` : null))}</span>
              <span>WR ${cell(fmtPct(lane.wr))}</span>
              <span>${lane.eta_days === null || lane.eta_days === undefined ? "ETA " + NA : "ETA " + esc(fmtNum(lane.eta_days, 1)) + "d"}</span>
            </div>
            <div class="lane-detail">
              <div class="kv"><span>Agent</span><b>${cell(detail.agent_id)}</b></div>
              <div class="kv"><span>Proposals</span><b>${cell(detail.proposals)}</b></div>
              <div class="kv"><span>Wins</span><b>${cell(lane.wins)}</b></div>
              <div class="kv"><span>7d accrual</span><b>${cell(fmtNum(lane.accrual_rate_7d, 3))} / day</b></div>
              ${skipPhrase && laneIsStarved ? `<div class="kv"><span>Dominant skip</span><b title="${esc(skipInfo.by_decision.map((d) => d.decision + " ×" + d.count).join(", "))}">${esc(skipPhrase)}</b></div>` : ""}
              ${skipInfo && laneIsStarved ? `<div class="kv"><span>Skip triage</span><b>${skipInfo.scope_only ? "all out-of-scope — idle by market, not broken" : "not all out-of-scope — possible fault"}</b></div>` : ""}
              ${detail.universe_widened_utc ? `<div class="kv"><span>Universe widened</span><b>${esc(detail.universe_widened_utc)}</b></div>` : ""}
              ${cal ? `<div class="kv"><span>Calendar</span><b>${esc(JSON.stringify(cal).slice(0, 240))}</b></div>` : ""}
              ${gateTable(detail.gate)}
            </div>
          </div>`;
        }).join("");
      }
    }
    renderGoals();
  }

  function renderGoals() {
    renderPerfStrip();
    const goals = state.goals || {};
    const host = $("goals-body");
    $("goals-age").innerHTML =
      state.errors.goals || goals.available === false ? "" : freshnessLine(goals.file, goals.generated_utc, 3600);

    if (state.errors.goals && !state.goals) {
      host.innerHTML = `<div class="alert danger">The goal feed failed to load (${esc(state.errors.goals)}). Transport failure — the goal data on disk is unknown, not empty.</div>`;
      $("goal-target").textContent = "";
      return;
    }
    if (goals.available === false) {
      host.innerHTML = `<div class="alert warn">${esc(goals.reason || "No goal report available.")}</div>`;
      $("goal-target").textContent = "";
      return;
    }
    const target = goals.target && typeof goals.target === "object" ? goals.target : null;
    // Render the goal line as the generator states it. The previous prefix read
    // target.wr_low / target.win_rate_low, neither of which the payload has (it
    // emits win_rate_band as an array), so both ?? operands were always
    // undefined and it hardcoded "Target band 59%-67%" -- a low bound matching
    // no constant in the repo (TARGET_WR_LOW is 0.63). It also headlined the
    // win-rate band as the target, the framing the 2026-08-02 retarget retired:
    // the goal is after-cost profitability, and goals.goal already says so.
    void target;
    $("goal-target").innerHTML = esc(goals.goal || "");

    const lanes = Array.isArray(goals.lanes) ? goals.lanes : [];
    if (!lanes.length) {
      host.innerHTML = `<div class="alert warn">The goal snapshot contains no lanes.</div>`;
      return;
    }

    // goal_progress.lanes is heterogeneous: probe lanes carry resolved/forward_wr,
    // paper-trading lanes carry closed_outcomes/win_rate/target_status. Render
    // whichever schema is present instead of blanking mismatched rows.
    host.innerHTML = lanes.map((g) => {
      const name = esc(g.lane || "lane");
      if (g.available === false) {
        return `<div class="goal-row"><strong>${name}</strong> <span class="na">lane not available: ${esc(g.reason || "no data")}</span></div>`;
      }
      const isProbe = g.forward_wr !== undefined || g.floor_progress !== undefined;
      if (isProbe && g.target_wr_low === undefined) {
        return `<div class="goal-row">
          <div class="goal-head"><strong>${name}</strong><span class="badge info">probe lane</span></div>
          <div class="goal-facts">
            <span>progress ${cell(g.floor_progress)}</span>
            <span>resolved ${cell(g.resolved)}</span>
            <span>wins ${cell(g.wins)}</span>
            <span>forward WR ${cell(fmtPct(g.forward_wr))}</span>
          </div>
        </div>`;
      }
      const status = String(g.target_status || "").toUpperCase();
      const statusCls =
        status === "TARGET_MET" ? "ok"
        : status === "INSUFFICIENT_SAMPLE" ? "neutral" : status ? "warn" : "";
      const ci = Array.isArray(g.win_rate_ci95) ? g.win_rate_ci95 : null;
      const net = num(g.net_after_cost_pnl);
      const strip = wrStrip({
        wr: g.win_rate, ci,
        bandLow: g.target_wr_low, bandHigh: g.target_wr_high,
      });
      return `<div class="goal-row">
        <div class="goal-head"><strong>${name}</strong>${
          status ? `<span class="badge ${statusCls}">${esc(status.replace(/_/g, " "))}</span>` : ""
        }${g.sample_mature === false ? `<span class="badge neutral" title="Fewer resolved outcomes than the maturity floor">immature sample</span>` : ""
        }${net !== null && net < 0 && num(g.closed_outcomes) > 0 ? `<span class="badge danger" title="Measured expectancy is negative after costs">NEGATIVE AFTER COST</span>` : ""}</div>
        ${num(g.target_wr_low) !== null ? strip : ""}
        <div class="goal-facts">
          <span>outcomes ${cell(g.closed_outcomes)}</span>
          <span>W/L ${cell(g.wins)} / ${cell(g.losses)}</span>
          <span>WR ${cell(fmtPct(g.win_rate))}</span>
          ${ci && num(ci[0]) !== null && num(ci[1]) !== null ? `<span>95% CI ${esc(fmtPct(ci[0]))}–${esc(fmtPct(ci[1]))}</span>` : `<span class="na">CI unavailable</span>`}
          <span class="${net !== null && net < 0 ? "neg" : net !== null && net > 0 ? "pos" : ""}">net after cost ${cell(fmtMoney(g.net_after_cost_pnl, 4))}</span>
          <span>expectancy ${cell(fmtMoney(g.expectancy_per_outcome, 4))}</span>
          <span>profit factor ${g.profit_factor === null || g.profit_factor === undefined ? (g.no_losses ? "n/a (no losses)" : NA) : esc(fmtNum(g.profit_factor, 2))}</span>
        </div>
      </div>`;
    }).join("");
  }

  /* ---------- performance strip (Overview: daily / weekly / monthly) ------ */
  // Renders the CANONICAL goal-report lanes — never recomputes WR/PnL from
  // positions or the warehouse. One source of truth: scripts/report_goal_progress
  // writes data/goal_progress.json; divergent dashboard math is how two-basis
  // confusion starts (see the wallet vs risk_state precedent). Staleness is
  // shown via freshnessLine; "Run goal report" in the rail refreshes it.
  const PERF_WINDOWS = [
    ["paper_futures_current_utc_day", "DAILY", "current UTC day"],
    ["paper_futures_7d", "WEEKLY", "rolling 7 days"],
    ["paper_futures_30d", "MONTHLY", "rolling 30 days"],
  ];

  function renderPerfStrip() {
    const host = $("perf-strip");
    if (!host) return;
    const age = $("perf-age");
    const goals = state.goals || {};
    if (age) age.innerHTML =
      state.errors.goals || goals.available === false ? "" : freshnessLine(goals.file, goals.generated_utc, 3600);

    if (state.errors.goals && !state.goals) {
      host.innerHTML = `<div class="alert danger">Goal feed failed to load (${esc(state.errors.goals)}) — daily/weekly/monthly figures are unknown, not zero.</div>`;
      return;
    }
    if (goals.available === false) {
      host.innerHTML = `<div class="alert warn">${esc(goals.reason || "No goal report on disk.")} “Run goal report” in the Operations rail generates it.</div>`;
      return;
    }
    const lanes = Array.isArray(goals.lanes) ? goals.lanes : [];
    host.innerHTML = PERF_WINDOWS.map(([key, label, hint]) => {
      const g = lanes.find((l) => l && l.lane === key);
      if (!g || g.available === false) {
        return `<div class="metric"><div class="k">${label} — ${esc(hint)}</div><div class="v"><span class="na">no data</span></div><div class="sub">lane absent from the goal report — run it from the rail</div></div>`;
      }
      const n = num(g.closed_outcomes);
      if (n === null || n === 0) {
        return `<div class="metric"><div class="k">${label} — ${esc(hint)}</div><div class="v"><span class="na">—</span></div><div class="sub">no closed trades in this window</div></div>`;
      }
      const net = num(g.net_after_cost_pnl);
      const netCls = net === null ? "" : net < 0 ? "neg" : net > 0 ? "pos" : "";
      const pf = g.profit_factor === null || g.profit_factor === undefined
        ? (g.no_losses ? "n/a (no losses)" : NA)
        : esc(fmtNum(g.profit_factor, 2));
      const status = String(g.target_status || "").replace(/_/g, " ").toLowerCase();
      return `<div class="metric">
        <div class="k">${label} — ${esc(hint)}</div>
        <div class="v">${cell(fmtPct(g.win_rate))}</div>
        <div class="sub">win rate · ${cell(g.wins)}W/${cell(g.losses)}L of ${cell(n)} · net <b class="${netCls} mono">${cell(fmtMoney(net, 2))}</b> · PF ${pf}${g.sample_mature === false ? " · immature sample" : ""}${status ? ` · ${esc(status)}` : ""}</div>
      </div>`;
    }).join("");
  }

  /* ---------- gates ---------- */

  function renderGates() {
    const g = state.gates;
    if (!g) {
      $("gate-grid").innerHTML = `<div class="alert danger">Gate feed unavailable.</div>`;
      return;
    }
    const items = [
      { k: "Checklist exists", v: g.checklist_exists ? "Yes" : "No", ok: !!g.checklist_exists },
      { k: "Checklist signed", v: g.checklist_signed ? "Signed" : "Not signed", ok: !!g.checklist_signed },
      { k: "Unchecked items",
        v: g.unchecked_count === null || g.unchecked_count === undefined ? null : String(g.unchecked_count),
        ok: (g.unchecked_count || 0) === 0 },
      { k: "CONTROLLED_LIVE_ENABLED", v: g.controlled_live_enabled ? "true" : "false", ok: !g.controlled_live_enabled },
      { k: "DRY_RUN (derived)", v: g.dry_run_derived ? "true" : "false", ok: !!g.dry_run_derived, note: g.dry_run_note },
      { k: "Operating mode (.env)", v: g.operating_mode || null, ok: (g.operating_mode || "") !== "CONTROLLED_LIVE" },
    ];
    $("gate-grid").innerHTML =
      items.map((i) =>
        `<div class="gate"><div class="k">${esc(i.k)}</div><div class="v ${i.ok ? "ok" : "danger"}">${i.v === null ? NA : esc(i.v)}</div>${i.note ? `<div class="sub">${esc(i.note)}</div>` : ""}</div>`).join("") +
      `<div class="gate wide"><div class="k">Gate message</div><div class="v msg">${esc(g.message || "")}</div>` +
      `<div class="sub">Arming/disarming lives in the Danger rail; it refuses while the checklist is unsigned. This console never writes checklist boxes or Signed-By lines.</div></div>`;
  }

  /* ---------- tasks ---------- */

  function renderTasks() {
    const tbody = $("tasks-table").querySelector("tbody");
    const note = $("tasks-note");
    if (!state.tasksLoaded) {
      note.textContent = "Not loaded — this query spawns a PowerShell process, so it runs on demand only.";
      tbody.innerHTML = `<tr><td colspan="6" class="empty">Press Reload to query Windows Task Scheduler.</td></tr>`;
      return;
    }
    const payload = state.tasks || {};
    if (payload.supported === false) {
      note.textContent = "Not applicable — scheduled tasks are a Windows-only feature.";
      tbody.innerHTML = `<tr><td colspan="6" class="empty">This host is not Windows.</td></tr>`;
      return;
    }
    if (payload.error) {
      note.textContent = "";
      tbody.innerHTML = `<tr><td colspan="6" class="empty error">Task query FAILED: ${esc(payload.error)} — this is not the same as "no tasks exist".</td></tr>`;
      return;
    }
    const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
    const q = state.taskFilter.trim().toLowerCase();
    const filtered = q ? tasks.filter((t) => String(t.Name || "").toLowerCase().includes(q)) : tasks;
    note.textContent = `${tasks.length} TradingBot* task(s) registered${q ? `, ${filtered.length} match the filter` : ""}. Times are Windows LOCAL time.`;
    tbody.innerHTML = filtered.length
      ? filtered.map((t) => {
          const res = taskResultLabel(t.LastResult);
          const name = String(t.Name || t.name || "");
          const allowed = name.startsWith("TradingBot");
          const runBtn = allowed
            ? `<button type="button" class="tiny" data-run-task="${esc(name)}" ${state.opsBusy ? "disabled" : ""}>Run now</button>`
            : `<button type="button" class="tiny" disabled title="Only TradingBot* tasks are allowlisted server-side">Run now</button>`;
          return `<tr>
            <td class="mono">${cell(name || null)}</td>
            <td>${cell(t.State)}</td>
            <td>${res.text === null ? NA : `<span class="badge ${res.cls}">${esc(res.text)}</span>`}</td>
            <td class="mono">${cell(t.LastRun)}</td>
            <td class="mono">${cell(t.NextRun)}</td>
            <td>${runBtn}</td>
          </tr>`;
        }).join("")
      : `<tr><td colspan="6" class="empty">${tasks.length ? "No tasks match the filter." : "Zero TradingBot* tasks are registered on this machine."}</td></tr>`;
  }

  /* ---------- candidates ---------- */

  function renderCandidates() {
    const host = $("candidates-body");
    const c = state.candidates;
    if (!c) return;
    if (!c.available) {
      host.innerHTML = `<div class="alert warn">${esc(c.reason || "Warehouse unavailable.")}</div>`;
      return;
    }
    const total = num(c.total_in_window) || 0;
    const decisions = Array.isArray(c.by_decision) ? c.by_decision : [];
    const skips = Array.isArray(c.top_skip_reasons) ? c.top_skip_reasons : [];
    if (!total) {
      host.innerHTML = `<div class="alert warn">No candidate rows in the last ${esc(c.window_hours)}h. The scoring loop evaluated nothing in this window — that is itself a finding.</div>`;
      return;
    }
    const skipTotal = decisions.filter((d) => d.decision === "SKIP").reduce((a, d) => a + d.count, 0);

    const decisionCards = decisions.map((d) =>
      `<div class="metric drill ${c.filter && c.filter.decision === d.decision ? "active" : ""}"
         data-drill-decision="${esc(d.decision)}" role="button" tabindex="0"
         title="Show only ${esc(d.decision)} rows below">
         <div class="k">${esc(d.decision)}</div>
         <div class="v">${d.count.toLocaleString()}</div>
         <div class="sub">${((d.count / total) * 100).toFixed(1)}% of ${total.toLocaleString()}</div>
       </div>`).join("");

    const families = Array.isArray(c.skip_families) ? c.skip_families : [];
    const maxFam = families.length ? families[0].count : 1;
    const famRows = families.map((f) =>
      `<tr class="drill ${c.filter && c.filter.family === f.family ? "active" : ""}" data-drill-family="${esc(f.family)}" role="button" tabindex="0" title="Show only rows skipped for this reason">
        <td>${esc(f.family)}</td>
        <td class="num mono">${f.count.toLocaleString()}</td>
        <td class="num mono">${skipTotal ? ((f.count / skipTotal) * 100).toFixed(1) : "—"}%</td>
        <td class="barcell"><span style="width:${((f.count / maxFam) * 100).toFixed(1)}%"></span></td>
        <td class="hint">${esc(f.variants)} variant(s), e.g. ${esc(f.example)}</td>
      </tr>`).join("");

    const maxSkip = skips.length ? skips[0].count : 1;
    const skipRows = skips.map((s) =>
      `<tr><td>${esc(s.reason)}</td><td class="num mono">${s.count.toLocaleString()}</td><td class="barcell"><span style="width:${((s.count / maxSkip) * 100).toFixed(1)}%"></span></td></tr>`).join("");

    const f = c.filter && (c.filter.decision || c.filter.family) ? c.filter : null;
    const recent = Array.isArray(c.recent) ? c.recent : [];
    const recentRows = recent.map((r) =>
      `<tr><td class="mono">${cell(fmtIsoLocal(r.ts_utc))}</td><td class="mono">${cell(r.symbol)}</td><td>${cell(r.exchange)}</td><td>${cell(r.side)}</td><td>${cell(r.decision)}</td><td>${cell(r.skip_reason)}</td></tr>`).join("");
    const filterNote = f
      ? `<span class="drill-note">filtered to <strong>${esc(f.decision || f.family)}</strong> <button type="button" id="clear-drill" class="ghost tiny">clear</button></span>`
      : "";

    host.innerHTML = `
      <p class="hint">${esc(total.toLocaleString())} candidates evaluated since ${esc(fmtIsoLocal(c.since_utc))}.</p>
      <div class="metric-grid compact">${decisionCards}</div>
      <h3 class="sub">Why candidates were skipped — grouped by cause</h3>
      <div class="table-wrap">
        <table class="data"><thead><tr><th>Cause</th><th class="num">Count</th><th class="num">Share</th><th>Weight</th><th>Detail</th></tr></thead><tbody>${
          famRows || `<tr><td colspan="5" class="empty">No SKIP rows in this window.</td></tr>`}</tbody></table>
      </div>
      <details class="raw-fold">
        <summary>Verbatim skip reasons (${esc(c.distinct_skip_reasons)} distinct — most embed a live measurement)</summary>
        <div class="table-wrap tall">
          <table class="data"><thead><tr><th>Reason</th><th class="num">Count</th><th>Share</th></tr></thead><tbody>${
            skipRows || `<tr><td colspan="3" class="empty">No SKIP rows in this window.</td></tr>`}</tbody></table>
        </div>
      </details>
      <h3 class="sub">Most recent decisions ${filterNote}</h3>
      <div class="table-wrap">
        <table class="data"><thead><tr><th>When (local)</th><th>Symbol</th><th>Exchange</th><th>Side</th><th>Decision</th><th>Skip reason</th></tr></thead><tbody>${
          recentRows || `<tr><td colspan="6" class="empty">${f ? "No matching rows in this window." : "No rows."}</td></tr>`}</tbody></table>
      </div>`;

    const clear = $("clear-drill");
    if (clear) clear.addEventListener("click", () => { state.candFilter = null; loadCandidates(); });
  }

  /* ---------- brain ---------- */
  //
  // Fed by ONE endpoint, /api/brain, which rides the 12s poll.
  // MEASURED warm on the live warehouse: cascade ~21 ms (scorer GROUP BY on
  // idx_candidates_ts 5 ms · ALLOW ids 1 ms · decision_events rowid tail +
  // payload parse 14 ms · decision-log tail 1.4 ms) + ~1 ms for the research
  // half once its 300 s artifact cache is warm. Budget is 250 ms.
  //
  // The probe-lane strip below adds ZERO bytes: it renders `state.funnel`,
  // which the poll already fetches for the Shadow probes tab.

  // `n` is the number of rows the measurement was recorded on, and it is NOT
  // decoration: for a required-condition family it IS the pass count, so
  // dropping it (as this did until 2026-07-28) turned a passing-condition list
  // into something that read as the cause of rejection.
  function fmtMeasure(m) {
    const unit = m.unit || "";
    const lo = fmtNum(m.min, 3);
    const hi = fmtNum(m.max, 3);
    const mid = fmtNum(m.median, 3);
    const n = num(m.n);
    const tail = n === null ? "" : ` (n=${n.toLocaleString()})`;
    if (lo === null || hi === null) return null;
    if (lo === hi) return `${m.label} ${lo}${unit}${tail}`;
    return `${m.label} ${lo}${unit}–${hi}${unit}, median ${mid === null ? "?" : mid}${unit}${tail}`;
  }

  function measureLine(list) {
    const parts = (Array.isArray(list) ? list : []).map(fmtMeasure).filter(Boolean);
    return parts.length ? parts.join(" · ") : null;
  }

  // A measurement line whose values were recorded ONLY when the condition
  // passed must say so in the same breath, or it reads as the deciding value.
  function measureBlock(list, passingValues) {
    const line = measureLine(list);
    if (!line) return "";
    return passingValues
      ? `<div class="cnote pass-vals"><b>Values recorded when the condition PASSED:</b> ${esc(line)}</div>`
      : `<div class="cnote">${esc(line)}</div>`;
  }

  // The required-condition pass/fail split. The bot writes a condition's value
  // only when it PASSES, so the failing condition has no measurement anywhere
  // in the log — its failure count is derived server-side (guarded by a
  // checksum) and is the only place the deciding condition can be named.
  function requiredSplitBlock(rs) {
    if (!rs) return "";
    const total = num(rs.candidates) || 0;
    if (!rs.derivable) {
      return `<div class="reqsplit"><div class="reqhead">Which required condition failed — <b>not derivable</b></div>
        <div class="cnote">${esc(rs.note || "the log does not support a per-condition split")}</div></div>`;
    }
    const conds = Array.isArray(rs.conditions) ? rs.conditions : [];
    const un = rs.unnamed;
    const rows = conds.map((c) => {
      const share = num(c.fail_share);
      const pct = share === null ? 0 : share * 100;
      return `<div class="reqrow${c.failures ? "" : " nofail"}">
        <div class="rl">${esc(c.label)}</div>
        <div class="rbar"><span style="width:${pct.toFixed(1)}%"></span></div>
        <div class="rv mono">failed ${Number(c.failures).toLocaleString()} of ${total.toLocaleString()} <span class="cpct">${pct.toFixed(0)}%</span></div>
      </div>`;
    }).join("");
    const unRow = un
      ? `<div class="reqrow unnamed">
          <div class="rl"><span class="na">not recoverable from the log</span></div>
          <div class="rbar"><span style="width:100%"></span></div>
          <div class="rv mono">failed ${Number(un.failures).toLocaleString()} of ${total.toLocaleString()} <span class="cpct">100%</span></div>
        </div><div class="cnote">${esc(un.note)}</div>`
      : "";
    return `<div class="reqsplit">
      <div class="reqhead">Which required condition failed — <b class="mono">${Number(rs.failures).toLocaleString()}</b> failures across <b class="mono">${Number(rs.checks).toLocaleString()}</b> checks (${esc(rs.of)} per candidate × ${total.toLocaleString()} candidates)</div>
      ${rows}${unRow}
      <div class="cnote">Bars are share of <b>these ${total.toLocaleString()} candidates</b>. Derived: the bot records a condition's value only when it passes, so a condition absent from the string is the one that failed. Verified against the <span class="mono">n/of</span> counts the same rows carry.</div>
    </div>`;
  }

  // Reason strings are server data: the plain-language translation is shown
  // first, and the VERBATIM string always follows in mono so the translation
  // can never hide what the bot actually wrote.
  function reasonPair(raw, plain) {
    const rawTxt = raw === null || raw === undefined || raw === "" ? null : String(raw);
    const plainTxt = plain ? esc(plain) : null;
    if (!rawTxt) return plainTxt || NA;
    if (!plainTxt) return `<span class="mono raw-reason">${esc(rawTxt)}</span>`;
    return `${plainTxt} <span class="mono raw-reason" title="verbatim reason string as written by the bot">${esc(rawTxt)}</span>`;
  }

  // EVERY bar declares its base. The indented rows are subsets of `allowed`,
  // not of `scored`: drawing them against `scored` rendered "blocked
  // downstream" as 15.9% when the true rate is 90.6%, understating the single
  // most important fact on the tab by ~6x. The base name is printed next to
  // the percentage because two bases share one waterfall.
  function cascadeStep(label, count, total, cls, sub, opts) {
    const n = num(count);
    const t = num(total);
    const pct = n !== null && t !== null && t > 0 ? Math.min(100, (n / t) * 100) : null;
    const o = opts || {};
    const base = o.base || "scored";
    return `<div class="cstep ${o.sub ? "indent" : ""}">
      <div class="clabel">${esc(label)}${sub ? `<span class="cnote">${esc(sub)}</span>` : ""}</div>
      <div class="cbar ${cls}"><span style="width:${pct === null ? 0 : pct.toFixed(1)}%"></span></div>
      <div class="cval mono">${n === null ? NA : n.toLocaleString()}${
        pct === null ? "" : ` <span class="cpct">${pct.toFixed(1)}% of ${esc(base)}</span>`}</div>
    </div>`;
  }

  // The verdict line: the tab's headline question, answered above the fold in
  // one sentence, in the same idiom as the Overview tab's #guidance line.
  function renderBrainVerdict(c) {
    const el = $("brain-verdict");
    if (!el) return;
    const set = (cls, html) => { el.className = `guidance ${cls}`; el.innerHTML = html; };
    if (state.errors.brain && !state.brain)
      return set("danger", `The brain feed failed to load (${esc(state.errors.brain)}) — why nothing traded is <b>unknown</b>, not answered.`);
    if (!c) return set("warn", "Waiting for the first successful brain poll…");
    if (!c.available)
      return set("warn", esc(c.reason || "The decision cascade is unavailable, so this question is unanswered."));

    const s = c.scorer || {};
    const t = c.terminal || {};
    const mins = esc(c.window_minutes);
    const scored = num(s.scored) || 0;
    const allowed = num(s.allowed) || 0;
    if (!scored)
      return set("warn", `<b>Nothing was scored</b> in the last ${mins} minutes — the scoring loop is idle, which is itself the finding. Check the heartbeat and the universe on the Overview tab.`);

    const filled = num(t.filled) || 0;
    const blocked = num(t.blocked) || 0;
    const deferred = num(t.deferred) || 0;
    const residual = num(t.residual) || 0;
    const bd = (c.binding || {}).downstream;
    const bs = (c.binding || {}).scorer;
    const ctx = c.context || {};
    const parts = [];
    if (ctx.interpretation) {
      parts.push(esc(ctx.interpretation));
      if (ctx.scorer_mode) {
        parts.push(
          `Scorer mode <b>${esc(ctx.scorer_mode)}</b>` +
          (ctx.allow_tradfi
            ? `; ALLOW split crypto=${num(ctx.allow_crypto) || 0} / tradfi=${num(ctx.allow_tradfi) || 0}`
            : "") +
          "."
        );
      }
    }
    parts.push(filled
      ? `<b>${filled.toLocaleString()} opened</b> in the last ${mins} minutes.`
      : `<b>Nothing opened</b> in the last ${mins} minutes.`);

    if (allowed) {
      const rate = ((blocked / allowed) * 100).toFixed(1);
      parts.push(`Of the ${allowed.toLocaleString()} candidates the scorer allowed, <b>${blocked.toLocaleString()} (${rate}%) were blocked downstream</b>, ${filled.toLocaleString()} filled, ${deferred.toLocaleString()} deferred and ${residual.toLocaleString()} have no terminal event yet.`);
      if (t.tail_complete === false)
        parts.push(`Those terminal figures are <b>truncated</b> — the event tail did not reach the start of the window, so they understate.`);
      // The plain strings already start with their own verb ("Blocked — …"),
      // so the frame must not supply a second one.
      if (bd)
        parts.push(`The largest single cause: <b>${esc(bd.plain || bd.family)}</b> (${bd.count.toLocaleString()} of the ${blocked.toLocaleString()} blocked).`);
      else if (blocked)
        parts.push(`No single gate dominates the blocks.`);
    } else {
      parts.push(`The scorer allowed <b>nothing</b>, so nothing reached a downstream gate.`);
    }
    if (s.aggregation_available === false) {
      parts.push(`At the scorer, ${(num(s.skipped) || 0).toLocaleString()} of ${scored.toLocaleString()} were skipped; <b>the reason breakdown could not be read</b>, so the scorer's binding condition is unknown.`);
    } else if (bs) {
      const tf = bs.top_failure;
      parts.push(`At the scorer, ${(num(s.skipped) || 0).toLocaleString()} of ${scored.toLocaleString()} were skipped — mostly <b>${esc(bs.plain || bs.family)}</b>${
        tf ? `, and the required condition failing most often is <b>${esc(tf.label)}</b> (${Number(tf.failures).toLocaleString()} of the ${(num(bs.count) || 0).toLocaleString()} in that cause)` : ""}.`);
    }
    set(filled ? "ok" : "warn", parts.join(" "));
  }

  function renderBrainCascade(c) {
    const host = $("brain-cascade");
    const win = $("brain-window");
    if (state.errors.brain && !state.brain) {
      win.textContent = "";
      host.innerHTML = `<div class="alert danger">The brain feed failed to load (${esc(state.errors.brain)}). This is a transport failure — the cascade on disk is unknown, not empty.</div>`;
      return;
    }
    if (!c) { win.textContent = ""; return; }
    if (!c.available) {
      win.textContent = "";
      host.innerHTML = `<div class="alert warn">${esc(c.reason || "The decision cascade is unavailable.")}</div>`;
      return;
    }
    const s = c.scorer || {};
    const t = c.terminal || {};
    const scored = num(s.scored) || 0;
    const allowed = num(s.allowed) || 0;
    win.innerHTML = `Rolling ${esc(c.window_minutes)}-minute window since ${esc(fmtIsoLocal(c.since_utc))}. Every count below is a subset of the one above it — the two stages join on <code>candidate_id</code>, so the arithmetic closes. <b>The three indented rows are shares of <em>allowed</em>, not of <em>scored</em></b>: each percentage names its own base.`;

    if (!scored) {
      host.innerHTML = `<div class="alert warn">No candidates were scored in the last ${esc(c.window_minutes)} minutes. An idle scoring loop is itself a finding — check the heartbeat and the universe on the Overview tab.</div>`;
      return;
    }

    const aggWarn = s.aggregation_available === false
      ? `<div class="alert warn">${esc(s.aggregation_note || "Reason-level breakdown unavailable.")} The decision totals below are still exact.</div>`
      : "";
    const tailWarn = t.tail_complete === false
      ? `<div class="alert warn">Terminal counts are TRUNCATED: the ${esc(t.tail_rows)}-row event tail did not reach the start of this window, so blocked/filled figures understate the true count. Narrow the window to read them exactly.</div>`
      : "";

    const waterfall = [
      cascadeStep("Candidates scored", scored, scored, "all", "every symbol the scorer evaluated"),
      cascadeStep("Skipped at the scorer", s.skipped, scored, "skip", "failed the entry conditions"),
      cascadeStep("Allowed by the scorer", allowed, scored, "allow", "passed scoring — not yet an order"),
      cascadeStep("↳ blocked by a downstream gate", t.blocked, allowed, "block", null, { sub: true, base: "allowed" }),
      cascadeStep("↳ deferred (maker order resting)", t.deferred, allowed, "defer", null, { sub: true, base: "allowed" }),
      cascadeStep("↳ opened / filled", t.filled, allowed, "fill", null, { sub: true, base: "allowed" }),
      num(t.other) ? cascadeStep("↳ other terminal outcome", t.other, allowed, "other", null, { sub: true, base: "allowed" }) : "",
      // The label is conditional on tail_complete: when the tail DID reach the
      // window start, "outside the event tail" is ruled out and claiming it
      // would explain the number away instead of reporting it.
      cascadeStep("↳ no terminal event recorded", t.residual, allowed, "residual",
        t.tail_complete === false
          ? "still pending, or outside the event tail"
          : "allowed, but no terminal event was written for it",
        { sub: true, base: "allowed" }),
    ].join("");

    // The conversion rate as a NUMBER, not only as a bar length.
    const conv = allowed
      ? `<p class="hint conv">Conversion: <b class="mono">${(num(t.filled) || 0).toLocaleString()} of ${allowed.toLocaleString()}</b> allowed candidates opened (<b>${(((num(t.filled) || 0) / allowed) * 100).toFixed(1)}%</b>); <b class="mono">${(num(t.blocked) || 0).toLocaleString()} of ${allowed.toLocaleString()}</b> were blocked downstream (<b>${(((num(t.blocked) || 0) / allowed) * 100).toFixed(1)}%</b>). Scoring admitted ${((allowed / scored) * 100).toFixed(1)}% of everything it saw.${
          t.tail_complete === false ? " <b>These two rates are floors</b>: the event tail did not reach the start of the window, so both terminal counts understate." : ""}</p>`
      : "";

    // Both tables: ONE proportional mark per row, drawn against the stated
    // total (not against the largest row), so the bar and the percentage
    // beside it are the same quantity on the same base.
    const causes = Array.isArray(s.skip_causes) ? s.skip_causes : [];
    const skipTotal = num(s.skipped) || 0;
    const causeRows = causes.map((f) => {
      const share = skipTotal ? (f.count / skipTotal) * 100 : null;
      const req = f.required_of && f.required_met
        ? `${fmtNum(f.required_met.median, 0)} of ${f.required_of} required conditions met (median)`
        : null;
      return `<tr>
        <td data-label="Cause">${reasonPair(f.family, f.plain)}${
          req ? `<div class="cnote">${esc(req)}</div>` : ""}${
          requiredSplitBlock(f.required_split)}${
          measureBlock(f.measurements, f.measurements_are_passing_values)}</td>
        <td class="num mono" data-label="Count">${f.count.toLocaleString()}</td>
        <td class="sharecell" data-label="Share of all skips">
          <span class="sv mono">${share === null ? "—" : share.toFixed(1) + "%"}</span>
          <span class="sb"><span style="width:${share === null ? 0 : share.toFixed(1)}%"></span></span>
        </td>
        <td class="hint" data-label="Detail">${esc(f.variants)} variant(s), e.g. <span class="mono">${esc(f.example)}</span></td>
      </tr>`;
    }).join("");

    const blocks = Array.isArray(t.blocks) ? t.blocks : [];
    const blockedTotal = num(t.blocked) || 0;
    const blockRows = blocks.map((b) => {
      const share = blockedTotal ? (b.count / blockedTotal) * 100 : null;
      const ofAllowed = allowed ? (b.count / allowed) * 100 : null;
      return `<tr>
        <td data-label="Gate">${reasonPair(b.reason, b.plain)}</td>
        <td class="mono" data-label="Stage">${cell(b.stage || null)}</td>
        <td class="num mono" data-label="Count">${b.count.toLocaleString()}</td>
        <td class="sharecell" data-label="Share of all blocks">
          <span class="sv mono">${share === null ? "—" : share.toFixed(1) + "%"}</span>
          <span class="sb"><span style="width:${share === null ? 0 : share.toFixed(1)}%"></span></span>
          <span class="s2">${ofAllowed === null ? "" : `= ${ofAllowed.toFixed(1)}% of the ${allowed.toLocaleString()} allowed`}</span>
        </td>
      </tr>`;
    }).join("");

    host.innerHTML = `${aggWarn}${tailWarn}
      <div class="cascade">${waterfall}</div>
      ${conv}
      <h3 class="sub">Stage 1 — why the scorer skipped them</h3>
      <div class="table-wrap grow">
        <table class="data stack-sm"><thead><tr><th>Cause</th><th class="num">Count</th><th>Share of all ${skipTotal.toLocaleString()} skips</th><th>Detail</th></tr></thead><tbody>${
          causeRows || `<tr><td colspan="4" class="empty${
            s.aggregation_available === false ? " error" : ""}">${
            // "unavailable" and "zero" are DIFFERENT answers. When the
            // aggregation could not run, the skip count above is still exact,
            // so an empty table here must never claim nothing was skipped.
            s.aggregation_available === false
              ? "Reason-level breakdown unavailable — the causes are unknown, not absent. " + esc(num(s.skipped) || 0) + " candidate(s) were skipped in this window."
              : "No SKIP rows in this window — that is a real answer, not missing data."
          }</td></tr>`}</tbody></table>
      </div>
      <h3 class="sub">Stage 2 — what blocked the ones it allowed</h3>
      <div class="table-wrap grow">
        <table class="data stack-sm"><thead><tr><th>Gate</th><th>Stage</th><th class="num">Count</th><th>Share of all ${blockedTotal.toLocaleString()} blocks</th></tr></thead><tbody>${
          blockRows || `<tr><td colspan="4" class="empty">${
            allowed ? "No terminal blocks recorded for this window's allowed candidates." : "The scorer allowed nothing in this window, so nothing reached a downstream gate."}</td></tr>`}</tbody></table>
      </div>
      <p class="hint">Stage 2 is joined to stage 1 by <code>candidate_id</code>, so only the allowed candidates of THIS window are counted — the two stages are not independent tallies. Longer windows and the full verbatim reason list live on the <strong>Why no trades</strong> tab.</p>`;
  }

  function renderBrainBinding(c) {
    const host = $("brain-binding");
    if (state.errors.brain && !state.brain) {
      host.innerHTML = `<div class="alert danger">Feed failed (${esc(state.errors.brain)}) — the binding constraint is unknown, not absent.</div>`;
      return;
    }
    if (!c || !c.available) { host.innerHTML = `<div class="hint">${NA}</div>`; return; }
    const b = c.binding || {};
    // An absent binding constraint means one of two very different things, and
    // saying "nothing was rejected" when the aggregation simply could not run
    // would be the unavailable-vs-zero error this console exists to avoid.
    const degraded = c.scorer && c.scorer.aggregation_available === false;
    const card = (title, x, hint, unknownWhenDegraded) => {
      if (!x) {
        const msg = degraded && unknownWhenDegraded
          ? "reason-level breakdown unavailable — the binding constraint is unknown, not absent"
          : "nothing rejected at this stage in the window";
        const sub = degraded && unknownWhenDegraded
          ? (c.scorer.aggregation_note || "")
          : hint;
        return `<div class="bind-card"><div class="k">${esc(title)}</div><div class="v"><span class="na">${esc(msg)}</span></div><div class="sub">${esc(sub)}</div></div>`;
      }
      const share = num(x.share_of_stage);
      const req = x.required_of && x.required_met
        ? `median ${fmtNum(x.required_met.median, 0)} of ${x.required_of} required conditions met`
        : null;
      // The DECIDING condition, when the family only logs the passing ones.
      const tf = x.top_failure;
      const decided = tf
        ? `<div class="sub decided">Deciding condition: <b>${esc(tf.label)}</b> — failed on <b class="mono">${Number(tf.failures).toLocaleString()}</b> of ${x.count.toLocaleString()} (${((num(tf.fail_share) || 0) * 100).toFixed(0)}%).</div>`
        : (x.required_split && x.required_split.derivable === false
          ? `<div class="sub decided na">Which required condition decided it cannot be derived from this log — ${esc(x.required_split.note || "")}</div>`
          : (x.required_split && x.required_split.unnamed
            ? `<div class="sub decided na">The deciding condition failed on every one of these candidates and is <b>not named in the log</b> — the bot records a condition's value only when it passes.</div>`
            : ""));
      return `<div class="bind-card"><div class="k">${esc(title)}</div>
        <div class="v">${reasonPair(x.family, x.plain)}</div>
        <div class="sub"><b class="mono">${x.count.toLocaleString()}</b> candidates${
          share === null ? "" : ` · ${(share * 100).toFixed(1)}% of everything rejected at this stage`}${
          req ? ` · ${esc(req)}` : ""}</div>
        ${decided}
        ${measureBlock(x.measurements, x.measurements_are_passing_values)}</div>`;
    };
    host.innerHTML = `<div class="bind-grid">
        ${card("Binding at the scorer", b.scorer, "no candidate was skipped by the scorer", true)}
        ${card("Binding downstream", b.downstream, "no allowed candidate was blocked by a gate", false)}
      </div>
      <p class="hint">Reported per stage on purpose. These are usually different conditions, and quoting only the scorer's would name the wrong constraint while the thing actually preventing trades sits downstream. Where a reason string lists only the conditions that <em>passed</em>, the deciding condition is derived from what is <em>missing</em> and labelled as such — the measurements shown are the passing values, never the value that caused the rejection.</p>`;
  }

  function renderBrainFeed(c) {
    const host = $("brain-feed");
    if (state.errors.brain && !state.brain) {
      host.innerHTML = `<div class="alert danger">Feed failed (${esc(state.errors.brain)}) — the live reasoning log was not read, which is not the same as the bot being silent.</div>`;
      return;
    }
    const live = (c && c.live) || null;
    if (!live) { host.innerHTML = `<div class="hint">${NA}</div>`; return; }
    if (!live.available) {
      host.innerHTML = `<div class="alert warn">${esc(live.reason || "The decision log could not be read.")}</div>`;
      return;
    }
    const intents = Array.isArray(live.intents) ? live.intents : [];
    const shown = state.brainFeedAll ? intents : intents.slice(0, 8);
    const rows = shown.map((i) => {
      const o = i.outcome;
      const fate = o
        ? `<span class="fate blocked">${reasonPair(o.reason, o.plain)}</span>`
        : `<span class="fate pending">no terminal record in this tail — pending or filled</span>`;
      return `<div class="think-row">
        <div class="think-top">
          <span class="mono think-sym">${cell(i.symbol)}</span>
          <span class="side-pill ${String(i.side || "").toLowerCase() === "sell" ? "short" : "long"}">${cell(i.type)} ${cell(i.side)}</span>
          <span class="mono think-when">${cell(fmtIsoLocal(i.ts_utc))}</span>
        </div>
        <div class="think-facts">
          <span>venue ${cell(i.exchange)}</span>
          <span>score ${cell(fmtNum(i.mcp_score, 0))}</span>
          <span>confidence ${cell(fmtNum(i.confidence, 2))}</span>
          <span>P(win) ${cell(fmtPct(i.p_win_ensemble, 1))}</span>
          <span>lev ${cell(fmtNum(i.leverage, 2))}×</span>
          <span>SL ${cell(fmtNum(i.sl_pct, 2))}% / TP ${cell(fmtNum(i.tp_pct, 2))}%</span>
        </div>
        <div class="think-reason mono">${cell(i.reason)}</div>
        <div class="think-fate">→ ${fate}</div>
      </div>`;
    }).join("");
    // EXIT decisions. The monitor returns CLOSE at 0.99 on the -12% backstop
    // and CLOSE at 0.88 on trend-reversal-plus-loss, so excluding monitor
    // cycles would give forced exits on losing positions zero coverage. What
    // is reported here is measured from THIS tail, never asserted of history.
    const mon = live.monitor || {};
    const exits = Array.isArray(mon.exits) ? mon.exits : [];
    const exitRows = exits.map((e) => `<div class="think-row exit">
        <div class="think-top">
          <span class="mono think-sym">${cell(e.position)}</span>
          <span class="side-pill exit">${cell(e.action)}</span>
          <span class="mono think-when">${cell(fmtIsoLocal(e.ts_utc))}</span>
        </div>
        <div class="think-facts"><span>confidence ${cell(fmtNum(e.confidence, 2))}</span><span>source ${cell(e.source)}</span></div>
        <div class="think-reason mono">${cell(e.reason)}</div>
      </div>`).join("");
    const monCycles = num(mon.cycles) || 0;
    const monDecisions = num(mon.decisions) || 0;
    const monHold = num(mon.hold) || 0;
    const found = num(mon.exits_found) || 0;
    // "every decision here was HOLD" is VACUOUSLY FALSE when the monitor
    // evaluated nothing: with no open position the cycle logs `decisions: {}`,
    // giving cycles>=1 with decisions=0. Asserting a property of an empty set
    // would be a fresh instance of the claim this panel was fixed to remove.
    const monLine = monDecisions === 0
      ? `${monCycles.toLocaleString()} monitor cycle(s) in this tail, <b>none of which had an open position to evaluate</b> — so this tail says nothing either way about how the monitor exits.`
      : `${monCycles.toLocaleString()} monitor cycle(s) carrying ${monDecisions.toLocaleString()} position decision(s) in this tail: <b>${found.toLocaleString()} non-HOLD (exit) decision(s)</b>${
          found > exits.length ? `, ${exits.length} shown` : ""} and ${monHold.toLocaleString()} HOLD.`;
    const monEmpty = monDecisions === 0
      ? `The monitor had nothing to decide on in this tail — that is an absence of positions, not an absence of exits.`
      : `No exit decision appears in this tail — all ${monDecisions.toLocaleString()} monitor decision(s) here were HOLD. That is a measured result for this window, not a property of the monitor.`;
    const exitBlock = `<h3 class="sub">Exit decisions from the position monitor</h3>
      <p class="hint">${monLine} The monitor can return CLOSE at confidence 0.99 on the −12% hard max-loss backstop and CLOSE at 0.88 on trend-reversal-plus-loss, so these are <b>not</b> assumed to be uninformative. This counts THIS tail only — <code>mcp_brain</code> rotates the log past 2 MB, so it is not a statement about history.</p>
      ${exitRows || `<div class="hint"><span class="na">${monEmpty}</span></div>`}`;

    const more = intents.length > shown.length || (state.brainFeedAll && intents.length > 8)
      ? `<p class="hint">Showing <b>${shown.length}</b> of <b>${intents.length}</b> entry intent(s) in this tail. <button type="button" class="tiny" id="brain-feed-toggle">${state.brainFeedAll ? "show fewer" : "show all"}</button></p>`
      : "";

    host.innerHTML = `<p class="hint">${esc(live.cycles)} cycle(s) in the last ${Math.round((num(live.bytes_read) || 0) / 1024)} KB of the decision log · ${esc(intents.length)} entry intent(s) found, newest first.</p>
      ${more}
      <div class="think-list">${rows || `<div class="hint"><span class="na">No entry intent appears in this tail. The bot was monitoring, not proposing entries.</span></div>`}</div>
      ${exitBlock}`;

    const toggle = $("brain-feed-toggle");
    if (toggle) toggle.addEventListener("click", () => {
      state.brainFeedAll = !state.brainFeedAll;
      renderBrainFeed(c);
    });
  }

  function renderBrainTelemetry(c) {
    const host = $("brain-telemetry");
    if (state.errors.brain && !state.brain) {
      host.innerHTML = `<div class="alert danger">Feed failed (${esc(state.errors.brain)}) — advisory state unknown.</div>`;
      return;
    }
    if (!c || !c.available) { host.innerHTML = `<div class="hint">${NA}</div>`; return; }
    const adv = Array.isArray(c.scorer && c.scorer.allow_advisories) ? c.scorer.allow_advisories : [];
    if (!adv.length) {
      host.innerHTML = `<div class="alert info">No meta-filter advisory is attached to any allowed candidate in this window.</div>`;
      return;
    }
    // Surface the worst numbers plainly. A loss streak of 20 is bad news and
    // belongs on the page, not behind a fold.
    const worst = {};
    adv.forEach((a) => (a.measurements || []).forEach((m) => {
      const v = num(m.value);
      if (v === null) return;
      if (!worst[m.key] || Math.abs(v) > Math.abs(worst[m.key].value)) worst[m.key] = { ...m, value: v };
    }));
    const chips = Object.keys(worst).map((k) => {
      const m = worst[k];
      const alarming = k === "loss_streak" && m.value >= 3;
      return `<div class="metric ${alarming ? "neg" : ""}"><div class="k">${esc(m.label)}</div><div class="v">${esc(fmtNum(m.value, 2))}${esc(m.unit || "")}</div><div class="sub">${
        alarming ? "consecutive losing trades — the meta-filter is flagging this, and it is not being hidden" : "as recorded on allowed candidates"}</div></div>`;
    }).join("");
    const rows = adv.map((a) =>
      `<tr><td>${reasonPair(a.reason, a.plain)}</td><td class="num mono">${a.count.toLocaleString()}</td></tr>`).join("");
    host.innerHTML = `<div class="metric-grid compact">${chips}</div>
      <div class="table-wrap">
        <table class="data"><thead><tr><th>Advisory annotation (on ALLOW rows)</th><th class="num">Count</th></tr></thead><tbody>${rows}</tbody></table>
      </div>`;
  }

  function renderBrainLanes() {
    const host = $("brain-lanes");
    const funnel = state.funnel || {};
    if (state.errors.funnel && !state.funnel) {
      host.innerHTML = `<div class="alert danger">The funnel feed failed to load (${esc(state.errors.funnel)}) — lane progress is unknown, not zero.</div>`;
      return;
    }
    if (funnel.available === false) {
      host.innerHTML = `<div class="alert warn">${esc(funnel.reason || "No funnel snapshot available.")} “Run promotion funnel” in the Operations rail writes it.</div>`;
      return;
    }
    const lanes = Array.isArray(funnel.lanes) ? funnel.lanes : [];
    if (!lanes.length) { host.innerHTML = `<div class="alert warn">The funnel snapshot contains no lanes.</div>`; return; }
    const floor = num(funnel.resolved_floor);
    host.innerHTML = `<div class="pipe-grid">${lanes.map((l) => {
      const name = String(l.lane || "lane");
      const resolved = num(l.resolved);
      const laneFloor = num(l.resolved_floor) ?? floor;
      const pct = resolved !== null && laneFloor !== null && laneFloor > 0
        ? Math.min(100, Math.round((resolved / laneFloor) * 100)) : null;
      const badge = laneBadge(l.state, name);
      const reached = pct !== null && pct >= 100;
      const gate = l.detail && l.detail.gate ? l.detail.gate : null;
      const gateWord = !reached ? "accruing" : gate && gate.passed === true ? "gate PASSED — owner sign-off required" : "gate did not pass";
      return `<div class="pipe-lane">
        <div class="pipe-top"><span class="mono">${esc(name)}</span><span class="badge ${badge.cls}" title="${esc(badge.why)}">${esc(badge.text)}</span></div>
        <div class="lanebar ${badge.bar}"><span style="width:${pct === null ? 0 : pct}%"></span></div>
        <div class="pipe-meta"><span>${cell(l.floor_progress || (resolved !== null && laneFloor !== null ? `${resolved}/${laneFloor}` : null))} resolved</span><span>WR ${cell(fmtPct(l.wr))}</span></div>
        <div class="pipe-stage">${esc(gateWord)} → promotion is owner-signed</div>
      </div>`;
    }).join("")}</div>`;
  }

  function renderBrainResearch() {
    const research = (state.brain && state.brain.research) || null;
    const ledgerHost = $("brain-ledger");
    const artHost = $("brain-artifacts");
    const caption = $("brain-artifacts-caption");
    if (state.errors.brain && !state.brain) {
      const msg = `<div class="alert danger">Feed failed (${esc(state.errors.brain)}) — this is a transport failure, not an empty pipeline.</div>`;
      ledgerHost.innerHTML = msg; artHost.innerHTML = msg; caption.textContent = "";
      return;
    }
    if (!research) return;

    const ledger = research.ledger || {};
    if (!ledger.available) {
      ledgerHost.innerHTML = `<div class="alert warn">${esc(ledger.reason || "The ledger could not be read.")} It lives at <code>${esc(ledger.path || "")}</code>.</div>`;
    } else {
      const counts = ledger.counts || {};
      const fam = ledger.families || {};
      const list = (key, label) => {
        const rows = Array.isArray(fam[key]) ? fam[key] : [];
        if (!rows.length) return "";
        return `<details class="raw-fold"><summary>${esc(label)} (${rows.length})</summary>
          <div class="table-wrap"><table class="data mini"><thead><tr><th>Family</th><th>Date</th></tr></thead><tbody>${
            rows.map((r) => `<tr><td>${esc(r.family)}</td><td class="mono">${esc(r.date)}</td></tr>`).join("")
          }</tbody></table></div></details>`;
      };
      ledgerHost.innerHTML = `<div class="metric-grid compact">
          ${metricCard("Refuted families", num(counts.refuted) === null ? null : String(counts.refuted), { sub: "pre-registered gates; do not re-propose without the reopen bar" })}
          ${metricCard("In shadow (log-only)", num(counts.shadow) === null ? null : String(counts.shadow), { sub: "hypotheses on trial — not validated, not tradeable" })}
          ${metricCard("Open — insufficient data", num(counts.open) === null ? null : String(counts.open), { sub: "not refuted, not screenable yet" })}
          ${metricCard("Reopen tests run", num(counts.reopen_tests) === null ? null : String(counts.reopen_tests), { sub: "cite these instead of re-searching" })}
        </div>
        <p class="hint">Full verdict evidence — the binding part — is in <code>${esc(ledger.path || "")}</code>. It is deliberately not duplicated here.</p>
        ${list("refuted", "Refuted families")}${list("shadow", "In shadow (log-only)")}${list("open", "Open — insufficient data")}`;
    }

    const art = research.artifacts || {};
    const promo = research.promotion || {};
    const promoLine = promo.exists
      ? `<div class="alert ${(promo.dossiers || []).length ? "info" : "warn"}">${
          (promo.dossiers || []).length
            ? `${esc((promo.dossiers || []).length)} promotion dossier(s) on disk in <code>${esc(promo.directory)}</code>.`
            : `<code>${esc(promo.directory)}</code> exists but contains no dossier.`} ${esc(promo.note || "")}</div>`
      : `<div class="alert info"><code>${esc(promo.directory || "reports/promotion_dossiers/")}</code> does not exist on this machine — no owner-signed dossier has ever been written. ${esc(promo.note || "")}</div>`;

    if (!art.available) {
      caption.textContent = "";
      artHost.innerHTML = `${promoLine}<div class="alert warn">${esc(art.reason || "The artifact directory could not be read.")}</div>`;
      return;
    }
    caption.innerHTML = `${esc(art.file_count)} file(s) in <code>${esc(art.directory)}</code>${
      art.cached ? ` · inventory cached ${esc(fmtAge(art.cache_age_seconds))} ago` : ""}. ${esc(art.caption || "")}`;
    const runs = Array.isArray(art.runs) ? art.runs : [];
    const recent = Array.isArray(art.recent) ? art.recent : [];
    artHost.innerHTML = `${promoLine}
      <div class="table-wrap">
        <table class="data"><thead><tr><th>Run</th><th>Artifact kinds present</th><th class="num">Files</th><th>Last touched</th></tr></thead><tbody>${
          runs.length ? runs.map((r) =>
            `<tr><td class="mono">${esc(r.run)}</td>
              <td>${r.kinds.length ? r.kinds.map((k) => `<span class="badge neutral">${esc(k)}</span>`).join(" ") : NA}${
                r.prereg_only ? `<span class="cnote">pre-registration only — no screen output file exists. That is a statement about files, not a claim that a screen is running.</span>` : ""}</td>
              <td class="num mono">${esc(r.files)}</td>
              <td class="mono">${cell(fmtIsoLocal(r.newest_utc))}</td></tr>`).join("")
            : `<tr><td colspan="4" class="empty">No numbered runs on disk.</td></tr>`}</tbody></table>
      </div>
      <details class="raw-fold"><summary>Most recently touched files (${recent.length})</summary>
        <div class="table-wrap"><table class="data mini"><thead><tr><th>File</th><th>First heading</th><th>Modified</th></tr></thead><tbody>${
          recent.map((f) =>
            `<tr><td class="mono">${esc(f.name)}</td><td>${f.heading ? esc(f.heading) : `<span class="na">no heading</span>`}</td><td class="mono">${cell(fmtIsoLocal(f.mtime_utc))}</td></tr>`).join("")
        }</tbody></table></div></details>`;
  }

  function renderBrain() {
    const payload = state.brain || null;
    const cascade = payload ? payload.cascade : null;
    renderBrainVerdict(cascade);
    renderBrainCascade(cascade);
    renderBrainBinding(cascade);
    renderBrainFeed(cascade);
    renderBrainTelemetry(cascade);
    renderBrainLanes();
    renderBrainResearch();
  }

  /* ---------- audit ---------- */

  function renderAudit() {
    const entries = (state.audit && state.audit.entries) || [];
    $("audit-list").innerHTML = entries.length
      ? entries.map((e) => {
          const when = e.ts || e.time || e.timestamp || "";
          const action = e.action || e.op || e.kind || "action";
          const note = e.operator_note || e.note || "";
          const detail = e.detail || e.result || "";
          const detailText = typeof detail === "string" ? detail : JSON.stringify(detail);
          const okCls = e.ok === true ? "ok" : e.ok === false ? "fail" : "";
          return `<div class="audit-row ${okCls}">
            <div class="when mono">${esc(when)}${e.ok === false ? ' · <span class="neg">refused/failed</span>' : ""}</div>
            <div><strong>${esc(action)}</strong>${note ? ` — ${esc(note)}` : ""}</div>
            ${detail ? `<div class="detail">${esc(detailText)}</div>` : ""}
          </div>`;
        }).join("")
      : `<div class="hint"><span class="na">No operator actions recorded yet (data/mission_control_audit.jsonl is absent or empty).</span></div>`;
  }

  /* ---------- ops rail ---------- */

  const PROFILE_SUGGESTIONS = ["STANDARD", "MAX_FLOW_BAND", "AGGRESSIVE_RESEARCH"];

  // Definitions for the 9 restored POST endpoints. `phrase` mirrors ops.py's
  // DESTRUCTIVE_CONFIRMS exactly; the server re-validates everything.
  function opDefs() {
    const s = state.status || {};
    const g = state.gates || {};
    const kill = !!s.kill_switch;
    const armed = !!g.controlled_live_enabled || (g.operating_mode || "") === "CONTROLLED_LIVE";
    return {
      "run-funnel": {
        group: "observe", label: "Run promotion funnel",
        sub: "scripts/promotion_funnel.py — rewrites data/promotion_funnel.json (can take minutes)",
        soft: true, path: () => "/api/ops/run-funnel",
        consequence: "Runs the promotion-funnel snapshot script now, in the foreground of the server. The funnel panel refreshes with the result.",
      },
      "run-goal": {
        group: "observe", label: "Run goal report",
        sub: "scripts/report_goal_progress.py — rewrites data/goal_progress.json",
        soft: true, path: () => "/api/ops/run-goal-report",
        consequence: "Runs the goal-progress report script now. The 59–67% band lanes refresh with the result.",
      },
      "profile": {
        group: "operate", label: "Set paper profile…",
        sub: `running: ${s.paper_profile || "?"} · .env: ${s.env_paper_profile || "?"}`,
        soft: true, path: () => "/api/ops/paper-profile",
        consequence: "Writes PAPER_TRADING_PROFILE into .env. The running bot does NOT pick this up until the supervisor is restarted — expect a divergence banner until then.",
        fields: [{ name: "profile", label: "Profile", type: "datalist", options: PROFILE_SUGGESTIONS, value: s.env_paper_profile || s.paper_profile || "" }],
        build: (fields) => ({ profile: fields.profile }),
      },
      "restart": {
        group: "danger", label: "Restart bot",
        sub: "ends + re-runs the TradingBot-24x7 task",
        phrase: "RESTART BOT", path: () => "/api/ops/restart",
        consequence: "Ends the TradingBot-24x7 scheduled task and runs it again. The worker dies mid-cycle and relaunches with a FRESH environment — this is the only way .env edits take effect.",
      },
      "mode": {
        group: "danger", label: "Change operating mode…",
        sub: `running: ${s.mode || "?"} — PAPER or OBSERVATION only`,
        phrase: "CHANGE MODE", path: () => "/api/ops/mode",
        consequence: "Writes OPERATING_MODE into .env and forces CONTROLLED_LIVE_ENABLED=false. OBSERVATION stops even paper orders. Takes effect only after a supervisor restart.",
        fields: [{ name: "mode", label: "Mode", type: "select", options: ["PAPER", "OBSERVATION"], value: s.env_mode || "PAPER" }],
        build: (fields) => ({ mode: fields.mode }),
      },
      "kill": {
        group: "danger",
        label: kill ? "Release kill switch" : "Engage kill switch",
        sub: kill ? "data/KILL_SWITCH exists — new entries paused" : "creates data/KILL_SWITCH — pauses NEW entries only",
        path: () => "/api/ops/kill-switch",
        consequence: kill
          ? "Removes data/KILL_SWITCH. The bot resumes opening NEW positions on its next cycle."
          : "Creates data/KILL_SWITCH. The bot refuses NEW entries but keeps managing open positions and their stops. This does not stop the process.",
        build: () => ({ enabled: !kill }),
      },
      "clear-incident": {
        group: "danger", label: "Clear incident latch",
        sub: s.incident_latch_present ? "latch PRESENT — entries halted" : null,
        disabled: s.incident_latch_present ? null : "no incident latch present",
        phrase: "CLEAR INCIDENT", path: () => "/api/ops/clear-incident",
        consequence: "Archives data/risk_incident_latch.json to risk_incident_history.jsonl and deletes it. Entries resume only after the supervisor is restarted. Clear it only after you understand why it latched.",
      },
      "live-arm": {
        group: "danger", label: "Arm CONTROLLED_LIVE",
        sub: armed ? "currently ARMED" : null,
        disabled: g.checklist_signed === false
          ? `checklist unsigned — ${g.unchecked_count ?? "?"} item(s) unchecked`
          : armed ? "already armed" : null,
        phrase: "ENABLE CONTROLLED LIVE", path: () => "/api/ops/controlled-live",
        consequence: "REAL MONEY. Sets OPERATING_MODE=CONTROLLED_LIVE and CONTROLLED_LIVE_ENABLED=true in .env. The server re-checks the signed checklist and refuses if it is not signed — this console never forges a signature.",
        build: () => ({ action: "arm" }),
      },
      "live-disarm": {
        group: "danger", label: "Disarm CONTROLLED_LIVE",
        hidden: !armed,
        path: () => "/api/ops/controlled-live",
        consequence: "Returns .env to PAPER / CONTROLLED_LIVE_ENABLED=false / DRY_RUN=true.",
        build: () => ({ action: "disarm" }),
      },
    };
  }

  function renderOps() {
    const defs = opDefs();
    const sig = JSON.stringify(Object.keys(defs).map((k) => [k, defs[k].sub, defs[k].disabled, defs[k].hidden, defs[k].label])) + (state.opsBusy || "");
    if (sig === state.opsSig) return;
    state.opsSig = sig;
    const groups = { observe: [], operate: [], danger: [] };
    Object.keys(defs).forEach((id) => {
      const d = defs[id];
      if (d.hidden) return;
      const disabled = d.disabled || (state.opsBusy ? "another operation is running" : null);
      groups[d.group].push(
        `<button type="button" class="op-btn" data-op="${esc(id)}" ${disabled ? `disabled` : ""}>
          ${esc(state.opsBusy === id ? d.label + " …" : d.label)}
          ${disabled ? `<small>${esc(disabled)}</small>` : d.sub ? `<small>${esc(d.sub)}</small>` : ""}
        </button>`);
    });
    // task runs live on the Tasks tab; note it here so the rail stays the map.
    groups.observe.push(`<button type="button" class="op-btn" data-op="goto-tasks"><small style="margin-top:0">Run a scheduled task → Tasks tab (per-task “Run now”)</small></button>`);
    $("ops-body").innerHTML = `
      <div class="ops-group"><div class="ops-group-title">Observe</div><div class="stack">${groups.observe.join("")}</div></div>
      <div class="ops-group"><div class="ops-group-title">Operate</div><div class="stack">${groups.operate.join("")}</div></div>
      <div class="ops-group danger-zone"><div class="ops-group-title">Danger</div><div class="stack">${groups.danger.join("")}</div></div>`;
  }

  /* ---------- confirm dialog (Cancel + Esc ALWAYS resolve) ---------- */

  let confirmActive = null;
  function confirmDialog(def) {
    if (confirmActive) return Promise.resolve(null);
    const dialog = $("confirm-dialog");
    const form = $("confirm-form");
    const phraseWrap = $("confirm-phrase-wrap");
    const phraseInput = $("confirm-phrase");
    const noteInput = $("confirm-note");
    const errEl = $("confirm-error");
    const fieldsHost = $("confirm-fields");

    $("confirm-title").textContent = def.label.replace(/…$/, "");
    $("confirm-consequence").textContent = def.consequence || "";
    dialog.className = def.group === "danger" ? "" : "soft";
    $("confirm-ok").textContent = def.group === "danger" ? "Execute" : "Run";

    fieldsHost.innerHTML = (def.fields || []).map((f) => {
      if (f.type === "select") {
        return `<label>${esc(f.label)}<select data-field="${esc(f.name)}">${f.options
          .map((o) => `<option value="${esc(o)}" ${o === f.value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
      }
      const listId = `dl-${f.name}`;
      return `<label>${esc(f.label)}<input data-field="${esc(f.name)}" value="${esc(f.value || "")}" list="${listId}" autocomplete="off" class="mono" />
        <datalist id="${listId}">${(f.options || []).map((o) => `<option value="${esc(o)}"></option>`).join("")}</datalist></label>`;
    }).join("");

    const needPhrase = !!def.phrase;
    phraseWrap.hidden = !needPhrase;
    $("confirm-phrase-expected").textContent = def.phrase || "";
    phraseInput.value = "";
    phraseInput.required = needPhrase;
    noteInput.value = "";
    errEl.hidden = true;
    dialog.returnValue = "";

    confirmActive = new Promise((resolve) => {
      let payload = null;
      const onSubmit = (ev) => {
        ev.preventDefault();
        const note = noteInput.value.trim();
        if (!note) { errEl.textContent = "Operator note is required — it is written to the audit log."; errEl.hidden = false; noteInput.focus(); return; }
        if (needPhrase && phraseInput.value.trim() !== def.phrase) {
          errEl.textContent = `Confirm phrase must be exactly “${def.phrase}”.`; errEl.hidden = false; phraseInput.focus(); return;
        }
        const fields = {};
        fieldsHost.querySelectorAll("[data-field]").forEach((el) => { fields[el.getAttribute("data-field")] = el.value.trim(); });
        payload = { note, phrase: needPhrase ? phraseInput.value.trim() : null, fields };
        dialog.close("ok");
      };
      const onCancel = () => dialog.close("");
      const onClose = () => {
        form.removeEventListener("submit", onSubmit);
        $("confirm-cancel").removeEventListener("click", onCancel);
        dialog.removeEventListener("close", onClose);
        confirmActive = null;
        resolve(dialog.returnValue === "ok" ? payload : null); // Esc/Cancel → null, promise never leaks
      };
      form.addEventListener("submit", onSubmit);
      $("confirm-cancel").addEventListener("click", onCancel);
      dialog.addEventListener("close", onClose);
      dialog.showModal();
      (needPhrase ? phraseInput : (fieldsHost.querySelector("[data-field]") || noteInput)).focus();
    });
    return confirmActive;
  }

  async function toastOpDone(label) {
    // Toast the audit line the op just wrote — the receipt, verbatim.
    try {
      const a = await api("/api/audit?limit=1");
      const e = (a.entries || [])[0];
      if (e) { toast(`${label} — done`, "ok", 8000, JSON.stringify(e)); return; }
    } catch { /* audit fetch is best-effort */ }
    toast(`${label} — done`, "ok", 6000);
  }

  async function runOp(id, overrides) {
    const def = opDefs()[id] || {};
    const merged = { ...def, ...(overrides || {}) };
    if (!merged.path || merged.disabled || state.opsBusy) return;
    const res = await confirmDialog(merged);
    if (!res) return;
    state.opsBusy = id;
    renderOps(); renderTasks();
    $("ops-rail").classList.add("busy");
    try {
      const body = { operator_note: res.note };
      if (merged.phrase) body.confirm_phrase = res.phrase;
      if (merged.build) Object.assign(body, merged.build(res.fields || {}, state));
      await apiPost(merged.path(state, res), body);
      await toastOpDone(merged.label.replace(/…$/, ""));
      await refresh();
      if (state.tasksLoaded) loadTasks();
    } catch (err) {
      toast(`${merged.label.replace(/…$/, "")} failed — ${err.message || err}`, "danger", 10000);
      try { const a = await api("/api/audit?limit=1"); state.audit = { ...(state.audit || {}), entries: [...(a.entries || []), ...((state.audit && state.audit.entries) || [])].slice(0, 100) }; } catch { /* best-effort */ }
    } finally {
      state.opsBusy = null;
      $("ops-rail").classList.remove("busy");
      renderOps(); renderTasks(); renderAudit();
    }
  }

  function runTask(name) {
    return runOp("run-task", {
      group: "observe", label: `Run task ${name}`, soft: true,
      consequence: `Triggers “schtasks /Run /TN ${name}” now. The task runs with its own configured action and account.`,
      path: () => `/api/ops/task/${encodeURIComponent(name)}/run`,
      disabled: null, hidden: false,
    });
  }

  /* ---------- orchestration ---------- */

  function renderAll() {
    renderErrors();
    renderGuidance();
    renderHeader();
    renderChips();
    renderOverview();
    renderPositions();
    renderHistory();
    renderRisk();
    renderProbes();
    renderBrain();
    renderGates();
    renderTasks();
    renderAudit();
    renderOps();
    state.lastRefreshAt = Date.now();
    if (!state.booted) { state.booted = true; document.body.classList.remove("boot"); }
    applyTicks(document);
    tickClock();
  }

  const FEEDS = [
    ["status", "/api/status"],
    ["positions", "/api/positions"],
    ["risk", "/api/risk"],
    ["funnel", "/api/funnel"],
    ["goals", "/api/goals"],
    ["gates", "/api/gates"],
    // MEASURED ~22 ms warm; rides the poll so the cascade is live rather than
    // a stale cache. Promise.allSettled isolates it like every other feed.
    ["brain", "/api/brain"],
    ["audit", "/api/audit?limit=100"],
  ];

  async function refresh() {
    if (state.inFlight) return;
    state.inFlight = true;
    try {
      const results = await Promise.allSettled(FEEDS.map(([, url]) => api(url)));
      results.forEach((r, i) => {
        const key = FEEDS[i][0];
        if (r.status === "fulfilled") {
          state[key] = r.value;
          state.fetchedAt[key] = Date.now();
          delete state.errors[key];
        } else {
          // Keep the last good payload and name the failure — never blank the page.
          state.errors[key] = String((r.reason && r.reason.message) || r.reason);
        }
      });
      renderAll();
    } finally {
      state.inFlight = false;
    }
  }

  async function loadTasks() {
    try {
      state.tasks = await api("/api/tasks");
      state.tasksLoaded = true;
      state.fetchedAt.tasks = Date.now();
      renderTasks();
    } catch (err) {
      state.tasks = { error: String(err.message || err), tasks: [] };
      state.tasksLoaded = true;
      renderTasks();
    }
  }

  async function loadCandidates() {
    const hours = $("cand-hours").value;
    const f = state.candFilter || {};
    const params = new URLSearchParams({ hours: String(hours), limit: "40" });
    if (f.decision) params.set("decision", f.decision);
    if (f.family) params.set("family", f.family);
    $("candidates-body").innerHTML = `<p class="hint">Querying the warehouse (read-only)…</p>`;
    try {
      state.candidates = await api(`/api/candidates?${params.toString()}`);
      renderCandidates();
    } catch (err) {
      $("candidates-body").innerHTML = `<div class="alert danger">Query failed: ${esc(err.message || err)}</div>`;
    }
  }

  function tickClock() {
    $("clock").textContent = new Date().toLocaleTimeString();
    if (state.lastRefreshAt) {
      const ago = Math.round((Date.now() - state.lastRefreshAt) / 1000);
      const label = ago < 2 ? "updated just now" : `updated ${ago}s ago`;
      const el = $("refresh-age");
      el.textContent = state.autoRefresh ? label : `${label} · polling paused`;
      el.className = `mono ${ago > REFRESH_MS / 1000 + 20 ? "stale" : "muted"}`;
    }
    // per-panel sync ages
    document.querySelectorAll(".sync[data-feed]").forEach((el) => {
      const key = el.getAttribute("data-feed");
      const at = state.fetchedAt[key];
      if (!at) { el.textContent = ""; return; }
      const s = Math.round((Date.now() - at) / 1000);
      el.textContent = `synced ${s < 2 ? "now" : fmtAge(s) + " ago"}`;
      el.classList.toggle("stale", s > (REFRESH_MS / 1000) * 3);
    });
  }

  /* ---------- tabs (proper tablist keyboard support) ---------- */

  const TAB_ORDER = ["overview", "positions", "history", "risk", "probes", "decisions", "brain", "gates", "tasks", "audit"];

  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      const on = t.getAttribute("data-tab") === name;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.setAttribute("tabindex", on ? "0" : "-1");
    });
    document.querySelectorAll(".view").forEach((v) => {
      v.classList.toggle("active", v.getAttribute("data-view") === name);
    });
    if (name === "tasks" && !state.tasksLoaded) loadTasks();
    if (name === "history") renderHistory(); // re-measure chart width now that it is visible
  }

  $("tabs").addEventListener("keydown", (ev) => {
    const idx = TAB_ORDER.indexOf(document.activeElement?.getAttribute?.("data-tab"));
    if (idx === -1) return;
    let next = null;
    if (ev.key === "ArrowRight") next = (idx + 1) % TAB_ORDER.length;
    else if (ev.key === "ArrowLeft") next = (idx - 1 + TAB_ORDER.length) % TAB_ORDER.length;
    else if (ev.key === "Home") next = 0;
    else if (ev.key === "End") next = TAB_ORDER.length - 1;
    if (next === null) return;
    ev.preventDefault();
    const btn = document.querySelector(`.tab[data-tab="${TAB_ORDER[next]}"]`);
    if (btn) { btn.focus(); switchTab(TAB_ORDER[next]); }
  });

  /* ---------- events ---------- */

  document.body.addEventListener("click", (ev) => {
    const tab = ev.target.closest(".tab");
    if (tab) return switchTab(tab.getAttribute("data-tab"));

    const opBtn = ev.target.closest("[data-op]");
    if (opBtn && !opBtn.disabled) {
      const id = opBtn.getAttribute("data-op");
      if (id === "goto-tasks") { switchTab("tasks"); closeRail(); return; }
      runOp(id);
      return;
    }

    const taskBtn = ev.target.closest("[data-run-task]");
    if (taskBtn && !taskBtn.disabled) { runTask(taskBtn.getAttribute("data-run-task")); return; }

    const drillDecision = ev.target.closest("[data-drill-decision]");
    if (drillDecision) {
      const d = drillDecision.getAttribute("data-drill-decision");
      state.candFilter = state.candFilter && state.candFilter.decision === d ? null : { decision: d };
      loadCandidates();
      return;
    }
    const drillFamily = ev.target.closest("[data-drill-family]");
    if (drillFamily) {
      const fam = drillFamily.getAttribute("data-drill-family");
      state.candFilter = state.candFilter && state.candFilter.family === fam ? null : { family: fam };
      loadCandidates();
      return;
    }

    const th = ev.target.closest("#history-table th[data-sort]");
    if (th) {
      const key = th.getAttribute("data-sort");
      state.historySort = state.historySort.key === key ? { key, dir: state.historySort.dir * -1 } : { key, dir: -1 };
      return renderHistory();
    }

    const lane = ev.target.closest(".lane");
    if (lane) {
      const name = lane.getAttribute("data-lane");
      if (state.openLanes.has(name)) state.openLanes.delete(name);
      else state.openLanes.add(name);
      lane.classList.toggle("open");
      lane.setAttribute("aria-expanded", lane.classList.contains("open"));
    }
  });

  document.body.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const target = ev.target.closest(".lane, [data-drill-decision], [data-drill-family]");
    if (!target) return;
    ev.preventDefault();
    target.click();
  });

  /* ---------- ops rail toggle (mobile) ---------- */

  function closeRail() {
    $("ops-rail").classList.remove("show");
    $("rail-scrim").hidden = true;
    $("btn-rail").setAttribute("aria-expanded", "false");
  }
  $("btn-rail").addEventListener("click", () => {
    const rail = $("ops-rail");
    const show = !rail.classList.contains("show");
    rail.classList.toggle("show", show);
    $("rail-scrim").hidden = !show;
    $("btn-rail").setAttribute("aria-expanded", String(show));
  });
  $("rail-scrim").addEventListener("click", closeRail);

  /* ---------- header controls + keyboard shortcuts ---------- */

  function setPaused(paused) {
    state.autoRefresh = !paused;
    const btn = $("btn-pause");
    btn.setAttribute("aria-pressed", String(paused));
    btn.textContent = paused ? "Resume" : "Pause";
    tickClock();
  }

  $("btn-refresh").addEventListener("click", async () => {
    await refresh();
    if (state.tasksLoaded) loadTasks();
    const errs = Object.keys(state.errors).length;
    toast(errs ? "Refreshed with errors — see banner" : "Refreshed", errs ? "danger" : "ok");
  });
  $("btn-pause").addEventListener("click", () => setPaused(state.autoRefresh));

  $("task-filter").addEventListener("input", (ev) => { state.taskFilter = ev.target.value || ""; renderTasks(); });
  $("btn-load-tasks").addEventListener("click", loadTasks);
  $("btn-candidates").addEventListener("click", loadCandidates);

  $("history-filter").addEventListener("input", (ev) => { state.historyFilter = ev.target.value || ""; renderHistory(); });
  $("history-range").addEventListener("change", (ev) => { state.historyRange = ev.target.value; renderHistory(); });

  $("btn-help").addEventListener("click", () => { $("help-drawer").hidden = false; $("help-close").focus(); });
  $("help-close").addEventListener("click", () => ($("help-drawer").hidden = true));
  $("help-drawer").addEventListener("click", (ev) => { if (ev.target === $("help-drawer")) $("help-drawer").hidden = true; });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !$("help-drawer").hidden) { $("help-drawer").hidden = true; return; }
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const t = ev.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    if ($("confirm-dialog").open || $("token-dialog").open) return;
    if (ev.key === "r" || ev.key === "R") { ev.preventDefault(); $("btn-refresh").click(); }
    else if (ev.key === "p" || ev.key === "P") { ev.preventDefault(); setPaused(state.autoRefresh); }
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (document.querySelector('.view[data-view="history"]').classList.contains("active")) renderHistory();
    }, 200);
  });

  setInterval(tickClock, 1000);
  setInterval(() => {
    if (!state.autoRefresh || document.hidden) return;
    refresh().catch((err) => {
      state.errors.refresh = String(err.message || err);
      renderErrors();
    });
  }, REFRESH_MS);

  (async () => {
    await ensureToken(false);
    await refresh();
    if (!state.status && Object.keys(state.errors).length) {
      const el = $("guidance");
      el.className = "guidance danger";
      el.textContent = `Could not load bot state: ${state.errors.status || "unknown error"}`;
    }
  })();
})();
