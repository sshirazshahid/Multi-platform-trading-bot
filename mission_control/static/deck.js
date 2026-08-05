/* Mission Deck — read-only cockpit.
 *
 * Contract with the rest of the system:
 *   - GET only. This file never calls /api/ops/*; operator actions stay in the
 *     classic console so there is exactly one mutating surface.
 *   - Renders what the API returns. It never recomputes a statistic that
 *     state.py already computes — notably win rate / PnL, which come from the
 *     canonical /api/goals lanes and NOT from positions.closed (a rolling
 *     500-row ring that silently truncates any window holding more trades).
 *     The only client-side aggregation is grouping skip-reason strings into
 *     families for display, which is labelled as such in the UI.
 *   - Shares localStorage["mission_control_token"] with the classic console,
 *     so a token entered in either surface works in both.
 */
(() => {
  "use strict";

  const TOKEN_KEY = "mission_control_token";

  /* Poll tiers, chosen from measured endpoint latency on this box:
   *   brain 175ms, risk 50ms                     -> fast
   *   positions 478ms (253KB), candidates 334ms  -> medium
   *   status 1408ms (psutil+subprocess), goals/funnel/gates 30-60ms -> slow
   * /api/tasks (1922ms, spawns schtasks) and /api/audit are deliberately not
   * polled here at all. */
  const TIERS = [
    { ms: 4000,  keys: ["brain", "risk"] },
    { ms: 9000,  keys: ["positions", "candidates"] },
    { ms: 20000, keys: ["status", "goals", "funnel", "gates"] },
  ];

  const S = Object.create(null);       // key -> payload
  const ERR = Object.create(null);     // key -> Error|null
  const GOT = Object.create(null);     // key -> epoch ms of last success
  const inflight = new Map();          // key -> Promise (single-flight)

  let paused = false;
  let authFailed = false;
  let wrLane = "paper_futures_7d";
  let candHours = 24;
  let seenFeed = new Set();
  let firstFeedRender = true;
  let renderQueued = false;

  /* ══════════ tiny helpers ══════════ */
  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);

  function money(v, digits = 2) {
    const n = num(v);
    if (n === null) return "—";
    const sign = n < 0 ? "-" : "";
    return `${sign}$${Math.abs(n).toFixed(digits)}`;
  }
  function pct(v, digits = 1) {
    const n = num(v);
    return n === null ? "—" : `${(n * 100).toFixed(digits)}%`;
  }
  function int(v) {
    const n = num(v);
    return n === null ? "—" : n.toLocaleString("en-US");
  }
  function age(sec) {
    const n = num(sec);
    if (n === null) return "—";
    if (n < 60) return `${Math.round(n)}s`;
    if (n < 3600) return `${Math.round(n / 60)}m`;
    if (n < 86400) return `${(n / 3600).toFixed(1)}h`;
    return `${(n / 86400).toFixed(1)}d`;
  }
  function hhmm(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return d.toISOString().slice(11, 19);
  }
  function tone(v, good = 0) {
    const n = num(v);
    if (n === null || n === good) return "mute";
    return n > good ? "good" : "bad";
  }

  /* Write text into an element and pulse it if the value actually changed. */
  function setVal(el, text) {
    if (!el) return;
    const changed = el.textContent !== text && el.textContent !== "" && el.textContent !== "—";
    el.textContent = text;
    if (changed) {
      el.classList.remove("bump");
      void el.offsetWidth;   // restart the animation
      el.classList.add("bump");
    }
  }

  function kpi(label, value, toneName, note) {
    return `<div class="kpi"><span class="k-label">${esc(label)}</span>` +
      `<span class="k-value" data-tone="${esc(toneName || "")}">${esc(value)}</span>` +
      (note ? `<span class="k-note">${esc(note)}</span>` : "") + `</div>`;
  }

  function toast(msg, toneName) {
    const stack = $("toasts");
    if (!stack) return;
    const el = document.createElement("div");
    el.className = "toast";
    if (toneName) el.dataset.tone = toneName;
    el.textContent = msg;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 6000);
  }

  /* ══════════ transport ══════════ */
  const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } };
  const setToken = (t) => { try { localStorage.setItem(TOKEN_KEY, t); } catch (e) { /* private mode */ } };

  class AuthError extends Error {}

  async function getJSON(path) {
    const token = getToken();
    const headers = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(path, { headers, cache: "no-store" });
    if (res.status === 401 || res.status === 403) throw new AuthError("token rejected");
    if (res.status === 503) throw new Error("MISSION_CONTROL_TOKEN not configured on the server");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  const PATHS = {
    status:     () => "/api/status",
    positions:  () => "/api/positions",
    risk:       () => "/api/risk",
    funnel:     () => "/api/funnel",
    goals:      () => "/api/goals",
    gates:      () => "/api/gates",
    brain:      () => "/api/brain",
    candidates: () => `/api/candidates?hours=${candHours}&limit=40`,
  };

  const OPTIONAL_FEEDS = new Set(); // reserved for soft-fail research feeds

  /* Single-flight: a slow response can never stack a second request behind it. */
  function pull(key) {
    if (inflight.has(key)) return inflight.get(key);
    const p = getJSON(PATHS[key]())
      .then((data) => { S[key] = data; ERR[key] = null; GOT[key] = Date.now(); })
      .catch((err) => {
        if (OPTIONAL_FEEDS.has(key)) {
          // Soft-fail: keep last good payload or an empty stub; never mark ERR.
          if (!S[key]) {
            S[key] = {
              present: false,
              final_verdict: "UNAVAILABLE",
              max_gross_inventory_usd: 1.0,
              inventory_usd: 0,
              honesty: "optional feed unavailable (stale Mission Control process or 404) — not a bot halt",
            };
          }
          ERR[key] = null;
        } else {
          ERR[key] = err;
        }
        if (err instanceof AuthError && !authFailed) {
          authFailed = true;
          askToken();
        }
      })
      .finally(() => { inflight.delete(key); scheduleRender(); });
    inflight.set(key, p);
    return p;
  }

  function pullAll(keys) {
    if (paused || document.hidden) return Promise.resolve();
    return Promise.allSettled(keys.map(pull));
  }

  function askToken() {
    const dlg = $("token-dialog");
    if (!dlg || dlg.open) return;
    $("token-input").value = "";
    dlg.showModal();
  }

  /* ══════════ render orchestration ══════════ */
  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => { renderQueued = false; render(); });
  }

  function render() {
    safe("rail", renderRail);
    safe("health", renderHealth);
    safe("verdict", renderVerdict);
    safe("actions", renderNextActions);
    safe("wr", renderWinRate);
    safe("pnl", renderEconomics);
    safe("wallet", renderWallet);
    safe("funnel", renderFunnel);
    safe("live", renderTelemetry);
    safe("binding", renderBinding);
    safe("feed", renderFeed);
    safe("positions", renderPositions);
    safe("lanes", renderLanes);
    safe("risk", renderRisk);
    markPanels();
  }

  /* One broken panel must never blank the deck. */
  function safe(name, fn) {
    try { fn(); }
    catch (err) { console.error(`[deck] ${name} render failed`, err); }
  }

  function markPanels() {
    document.querySelectorAll(".panel[data-panel]").forEach((el) => {
      const key = el.dataset.panel;
      el.dataset.error = ERR[key] ? "true" : "false";
      const got = GOT[key];
      el.dataset.stale = got ? String(Date.now() - got > 90000) : "false";
    });
  }

  /* ══════════ rail ══════════ */
  function renderRail() {
    const st = S.status || {};
    const hbAge = num(st.heartbeat_age_seconds);
    const pulse = $("hb-pulse");
    let state = "off";
    if (hbAge !== null) state = hbAge < 180 ? "ok" : hbAge < 900 ? "warn" : "bad";
    pulse.dataset.state = state;
    pulse.title = hbAge === null ? "No heartbeat file" : `Heartbeat ${age(hbAge)} old`;

    const mode = st.mode || "—";
    const badge = $("mode-badge");
    badge.textContent = mode;
    badge.dataset.live = String(!/PAPER|OBSERV/i.test(mode) && mode !== "—");
    $("profile-badge").textContent = st.paper_profile || "—";

    const conn = $("conn-state");
    const anyErr = Object.keys(ERR).some((k) => ERR[k]);
    const anyData = Object.keys(S).length > 0;
    if (paused) { conn.dataset.state = "paused"; conn.textContent = "paused"; }
    else if (authFailed) { conn.dataset.state = "down"; conn.textContent = "unauthorized"; }
    else if (anyErr) { conn.dataset.state = "stale"; conn.textContent = "degraded"; }
    else if (anyData) { conn.dataset.state = "live"; conn.textContent = "live"; }

    const errKeys = Object.keys(ERR).filter((k) => ERR[k]);
    $("poll-note").textContent = errKeys.length
      ? `feeds failing: ${errKeys.join(", ")}`
      : `polling ${TIERS.map((t) => `${t.ms / 1000}s`).join(" / ")}`;
  }

  /* ══════════ health strip + verdict + next actions ══════════ */
  function renderHealth() {
    const st = S.status || {};
    const rs = (S.risk || {}).risk_state || {};
    const open = ((S.positions || {}).open || []).length;
    const hbAge = num(st.heartbeat_age_seconds);
    const halted = !!(rs.is_halted || st.is_halted || st.incident_latch_present);
    const entriesOk = !halted && !st.kill_switch;
    const wrap = $("verdict-wrap");
    if (wrap) wrap.dataset.health = halted ? "bad" : (hbAge !== null && hbAge > 900 ? "warn" : "ok");

    const hbTone = hbAge === null ? "mute" : hbAge < 180 ? "good" : hbAge < 900 ? "warn" : "bad";
    $("health-strip").innerHTML = [
      kpi("Heartbeat", age(hbAge), hbTone, hbAge === null ? "unknown" : "ago"),
      kpi("Mode", st.mode || "—", String(st.mode || "").toUpperCase() === "CONTROLLED_LIVE" ? "bad" : "mute"),
      kpi("Profile", st.paper_profile || "—", "mute"),
      kpi("Open", int(open), open > 0 ? "mute" : "mute"),
      kpi("Entries", entriesOk ? "allowed" : "blocked", entriesOk ? "good" : "bad",
        halted ? "incident latch" : st.kill_switch ? "kill switch" : ""),
    ].join("");
  }

  function renderVerdict() {
    const lane = laneByName(wrLane);
    const scorer = (((S.brain || {}).cascade || {}).binding || {}).scorer;
    const parts = [];

    if (lane && lane.closed_outcomes > 0) {
      const cls = lane.target_status === "TARGET_MET" ? "good" : "bad";
      parts.push(`Over ${esc(laneLabel(wrLane))} the bot resolved <b>${int(lane.closed_outcomes)}</b> trades at ` +
        `<span class="${cls}">${pct(lane.win_rate)}</span> win rate for ` +
        `<span class="${lane.net_after_cost_pnl >= 0 ? "good" : "bad"}">${money(lane.net_after_cost_pnl)}</span> after cost.`);
    } else if (lane) {
      parts.push(`No resolved trades yet in ${esc(laneLabel(wrLane))}.`);
    }

    if (scorer && scorer.plain) {
      const top = scorer.top_failure;
      parts.push(`The binding constraint is <b>${esc(scorer.family || "—")}</b> — ${esc(scorer.plain)}` +
        (top ? `, driven mostly by <b>${esc(top.label || top.key)}</b> failing ${pct(top.fail_share, 0)} of the time.` : "."));
    }

    $("verdict").innerHTML = parts.join(" ") || "Waiting for the first successful poll…";

    const al = [];
    const st = S.status || {};
    const rs = (S.risk || {}).risk_state || {};
    const hbAge = num(st.heartbeat_age_seconds);
    if (rs.is_halted || st.is_halted) al.push(["bad", `HALTED — ${rs.halt_reason || st.halt_reason || "reason not recorded"}`]);
    if (st.incident_latch_present) al.push(["bad", "Risk incident latch present — new entries blocked"]);
    if (st.kill_switch) al.push(["bad", "Kill switch engaged"]);
    if (hbAge !== null && hbAge > 900) al.push(["bad", `Heartbeat stale (${age(hbAge)}) — process may be hung`]);
    else if (hbAge !== null && hbAge > 180) al.push(["warn", `Heartbeat aging (${age(hbAge)})`]);
    if (st.mode_divergent) al.push(["warn", "Mode differs from .env — restart supervisor to apply"]);
    if (st.profile_divergent) al.push(["warn", "Paper profile differs from .env — restart supervisor to apply"]);
    const g = S.gates || {};
    if (g.checklist_exists && !g.checklist_signed)
      al.push(["ok", `Real-money path fail-closed — checklist unsigned (${int(g.unchecked_count)} items)`]);
    const ddLane = laneByName(wrLane);
    if (ddLane && ddLane.drawdown_exceeds_cap)
      al.push(["warn",
        `${laneLabel(wrLane)} drawdown ${num(ddLane.max_drawdown_to_gross_profit) !== null
          ? ddLane.max_drawdown_to_gross_profit.toFixed(2) + "x"
          : ""} gross profit — over the ${num(ddLane.drawdown_cap_to_gross_profit) !== null
          ? ddLane.drawdown_cap_to_gross_profit.toFixed(2) + "x"
          : ""} cap`]);
    const lane7 = laneByName("paper_futures_7d");
    if (lane7 && lane7.target_status && lane7.target_status !== "TARGET_MET" && lane7.closed_outcomes > 0)
      al.push(["warn", `7d status: ${String(lane7.target_status).replace(/_/g, " ").toLowerCase()}`]);

    $("alarms").innerHTML = al.map((a) => `<span class="alarm" data-sev="${a[0]}">${esc(a[1])}</span>`).join("");
  }

  function renderNextActions() {
    const host = $("next-actions");
    if (!host) return;
    const st = S.status || {};
    const rs = (S.risk || {}).risk_state || {};
    const actions = [];

    if (rs.is_halted || st.is_halted || st.incident_latch_present) {
      actions.push({
        sev: "bad",
        title: "Clear incident latch",
        why: "New entries stay blocked until the latch is archived and removed.",
        href: "/classic?ops=clear-incident",
        label: "Open Clear incident",
      });
      actions.push({
        sev: "warn",
        title: "Restart supervisor",
        why: "After clearing, restart so the worker reloads code and drops in-memory halt state.",
        href: "/classic?ops=restart",
        label: "Open Restart bot",
      });
    } else if (st.kill_switch) {
      actions.push({
        sev: "bad",
        title: "Release kill switch",
        why: "KILL_SWITCH is pausing new entries; open positions still manage stops.",
        href: "/classic?ops=kill",
        label: "Open Kill switch",
      });
    } else if (st.mode_divergent || st.profile_divergent) {
      actions.push({
        sev: "warn",
        title: "Restart to apply .env",
        why: "Running mode/profile differs from .env — long-lived supervisors never re-read env.",
        href: "/classic?ops=restart",
        label: "Open Restart bot",
      });
    }

    const hbAge = num(st.heartbeat_age_seconds);
    if (hbAge !== null && hbAge > 900) {
      actions.push({
        sev: "bad",
        title: "Investigate hung process",
        why: `Heartbeat is ${age(hbAge)} old. Prefer restart after checking logs.`,
        href: "/classic?ops=restart",
        label: "Open Restart bot",
      });
    }

    if (!actions.length) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.hidden = false;
    host.innerHTML =
      `<p class="sec-label">Next action</p>` +
      actions.map((a) =>
        `<div class="action-card" data-sev="${esc(a.sev)}">` +
        `<div><strong>${esc(a.title)}</strong><p>${esc(a.why)}</p></div>` +
        `<a class="rbtn primary" href="${esc(a.href)}">${esc(a.label)}</a>` +
        `</div>`
      ).join("");
  }

  /* ══════════ win-rate gauge ══════════ */
  const LANE_LABELS = {
    paper_futures_current_utc_day: "today (UTC)",
    paper_futures_7d: "7 days",
    paper_futures_30d: "30 days",
  };
  const laneLabel = (n) => LANE_LABELS[n] || n;

  function laneByName(name) {
    const lanes = (S.goals || {}).lanes || [];
    return lanes.find((l) => l && l.lane === name) || null;
  }

  function polar(cx, cy, r, deg) {
    const rad = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
  }
  /* Semicircle sweeping left (180deg) to right (360deg); frac 0..1 along it. */
  const ANG = (frac) => 180 + 180 * Math.max(0, Math.min(1, frac));

  function arcPath(cx, cy, r, f0, f1) {
    const a = polar(cx, cy, r, ANG(f0));
    const b = polar(cx, cy, r, ANG(f1));
    const large = Math.abs(f1 - f0) > 0.5 ? 1 : 0;
    return `M ${a[0].toFixed(2)} ${a[1].toFixed(2)} A ${r} ${r} 0 ${large} 1 ${b[0].toFixed(2)} ${b[1].toFixed(2)}`;
  }

  function renderWinRate() {
    const lane = laneByName(wrLane);
    const target = (S.goals || {}).target || {};
    const band = target.win_rate_band || [0.59, 0.67];
    const svg = $("wr-gauge");
    const CX = 120, CY = 116, R = 92;

    const wr = lane ? num(lane.win_rate) : null;
    const ci = (lane && lane.win_rate_ci95) || [null, null];
    // Colour by PROFITABILITY, not band membership. Colouring by band made a
    // profitable lane at 80% WR render amber and an unprofitable lane at 65%
    // render green -- "success reported as a miss", the exact defect this
    // retarget removed from target_status, still live on the loudest pixel.
    // The band arc below stays as grey reference shading only.
    const st = lane ? String(lane.target_status || "") : "";
    const colour = wr === null || !lane ? "var(--ink-faint)"
      : st === "TARGET_MET" ? "var(--green)"
      : st === "INSUFFICIENT_SAMPLE" ? "var(--ink-faint)"
      : "var(--red)";

    let ticks = "";
    for (let t = 0; t <= 1.0001; t += 0.25) {
      const a = polar(CX, CY, R - 13, ANG(t));
      const b = polar(CX, CY, R - 5, ANG(t));
      const l = polar(CX, CY, R - 24, ANG(t));
      ticks += `<line class="g-tick" x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}" />` +
        `<text class="g-lab" x="${l[0].toFixed(1)}" y="${(l[1] + 3).toFixed(1)}" text-anchor="middle">${Math.round(t * 100)}</text>`;
    }

    let ciMarks = "";
    [ci[0], ci[1]].forEach((v) => {
      const n = num(v);
      if (n === null) return;
      const a = polar(CX, CY, R - 11, ANG(n));
      const b = polar(CX, CY, R + 11, ANG(n));
      ciMarks += `<line class="g-ci" x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}" />`;
    });

    svg.innerHTML =
      `<path class="g-track" d="${arcPath(CX, CY, R, 0, 1)}" stroke-width="14" />` +
      `<path class="g-band"  d="${arcPath(CX, CY, R, band[0], band[1])}" stroke-width="14" />` +
      ticks + ciMarks +
      `<path id="g-val" class="g-val" d="${arcPath(CX, CY, R, 0, 1)}" stroke-width="14" stroke="${colour}" />`;

    const valPath = svg.querySelector("#g-val");
    const L = valPath.getTotalLength();
    valPath.style.strokeDasharray = String(L);
    valPath.style.strokeDashoffset = String(wr === null ? L : L * (1 - wr));

    const v = $("wr-value");
    setVal(v, wr === null ? "—" : pct(wr, 1));
    v.style.color = colour;
    $("wr-ci").textContent = (num(ci[0]) !== null && num(ci[1]) !== null)
      ? `95% CI ${pct(ci[0], 1)} - ${pct(ci[1], 1)}`
      : (lane && lane.closed_outcomes === 0 ? "no resolved trades yet" : "—");
    $("wr-lane-label").textContent = laneLabel(wrLane);

    const floor = lane ? lane.mature_sample_floor : null;
    $("wr-stats").innerHTML = lane
      ? `<span>sample <b>${int(lane.closed_outcomes)}</b>${floor ? ` / ${int(floor)} floor` : ""}</span>` +
        `<span>W <b>${int(lane.wins)}</b></span><span>L <b>${int(lane.losses)}</b></span>` +
        // Breakeven replaces the old target band. The band was the terminal
        // success label, which rewarded manufacturing a win rate; what matters
        // is the win rate THIS lane's own realized payoff requires.
        (num(lane.breakeven_win_rate) !== null
          ? `<span>breakeven <b>${pct(lane.breakeven_win_rate, 1)}</b></span>`
          : `<span>breakeven <b>—</b></span>`) +
        // Guard on the field actually being rendered. Keying this off
        // breakeven_win_rate would, if the two ever decouple, print
        // null*100 -> "0.0 pp" coloured green (null >= 0 is true) -- a
        // fabricated "exactly at breakeven".
        (num(lane.win_rate_vs_breakeven) !== null
          ? `<span>gap <b class="${lane.win_rate_vs_breakeven >= 0 ? "pos" : "neg"}">` +
            `${(lane.win_rate_vs_breakeven * 100).toFixed(1)} pp</b></span>`
          : "") +
        (num(lane.max_drawdown_abs) !== null
          ? `<span>maxDD <b>${money(lane.max_drawdown_abs)}</b>` +
            (num(lane.max_drawdown_to_gross_profit) !== null
              ? ` <b class="${lane.drawdown_exceeds_cap ? "neg" : "pos"}">` +
                `${lane.max_drawdown_to_gross_profit.toFixed(2)}x GP</b>`
              : "") +
            `</span>`
          : "") +
        `<span>${lane.sample_mature ? "mature" : "below floor"}</span>`
      : "";
    $("wr-src").textContent = lane && lane.win_rate_note
      ? `source: /api/goals lane ${wrLane} · ${lane.win_rate_note}`
      : `source: /api/goals lane ${wrLane} · win rate is a diagnostic, not the target`;
  }

  /* ══════════ economics ══════════ */
  function renderEconomics() {
    const lane = laneByName(wrLane);
    const st = lane ? (lane.target_status || "—") : "—";
    const pill = $("pnl-status");
    pill.textContent = String(st).replace(/_/g, " ");
    pill.dataset.tone = st === "TARGET_MET" ? "good" : st === "INSUFFICIENT_SAMPLE" ? "" : "bad";

    const pf = lane ? num(lane.profit_factor) : null;
    $("pnl-kpis").innerHTML = lane ? [
      kpi("Net after cost", money(lane.net_after_cost_pnl), tone(lane.net_after_cost_pnl)),
      kpi("Expectancy", money(lane.expectancy_per_outcome, 3), tone(lane.expectancy_per_outcome), "per outcome"),
      kpi("Profit factor", pf === null ? "—" : pf.toFixed(2), pf === null ? "mute" : pf > 1 ? "good" : "bad", "need > 1.00"),
    ].join("") : `<div class="skel"></div>`;

    const gp = lane ? (num(lane.gross_profit) || 0) : 0;
    const gl = lane ? (num(lane.gross_loss) || 0) : 0;
    const span = Math.max(gp, gl, 1e-9);
    $("gl-bars").innerHTML = lane ? [
      ["Profit", gp, "var(--green)"],
      ["Loss", gl, "var(--red)"],
    ].map((row) =>
      `<div class="glbar"><span class="gl-label">${row[0]}</span>` +
      `<span class="gl-track"><span class="gl-fill" style="width:${((row[1] / span) * 100).toFixed(1)}%;background:${row[2]}"></span></span>` +
      `<span class="gl-val">${money(row[1])}</span></div>`).join("") : "";

    renderSpark();
  }

  function renderSpark() {
    const curve = (S.risk || {}).trade_history_curve || [];
    const svg = $("equity-spark");
    if (!curve.length) {
      svg.innerHTML = "";
      $("equity-src").textContent = "no closed-trade curve available";
      return;
    }
    const W = 320, H = 68, PAD = 4;
    const cums = curve.map((p) => num(p.cum) || 0);
    const lo = Math.min.apply(null, [0].concat(cums));
    const hi = Math.max.apply(null, [0].concat(cums));
    const range = (hi - lo) || 1;
    const x = (i) => PAD + (i / Math.max(1, curve.length - 1)) * (W - 2 * PAD);
    const y = (v) => H - PAD - ((v - lo) / range) * (H - 2 * PAD);

    const pts = cums.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const last = cums[cums.length - 1];
    const colour = last >= 0 ? "var(--green)" : "var(--red)";
    const zeroY = y(0).toFixed(1);

    svg.innerHTML =
      `<line class="sp-zero" x1="0" y1="${zeroY}" x2="${W}" y2="${zeroY}" />` +
      `<polygon class="sp-area" fill="${colour}" points="${x(0).toFixed(1)},${zeroY} ${pts} ${x(cums.length - 1).toFixed(1)},${zeroY}" />` +
      `<polyline class="sp-line" stroke="${colour}" points="${pts}" />`;
    $("equity-src").textContent =
      `cumulative net over ${curve.length} closed trades · ends ${money(last)} · source: /api/risk trade_history_curve`;
  }

  /* ══════════ wallet ══════════ */
  function renderWallet() {
    const ws = (S.positions || {}).wallet_summary || {};
    const balances = ws.balances || ((S.positions || {}).wallet || {}).balances || {};
    const total = num(ws.total);
    const seed = num(ws.seed_total);
    const delta = num(ws.delta_vs_seed);

    $("wallet-basis").textContent = ws.basis ? String(ws.basis) : "paper wallet";
    $("wallet-kpis").innerHTML = [
      kpi("Equity", total === null ? "—" : money(total), "mute"),
      kpi("vs seed", delta === null ? "—" : money(delta), tone(delta), seed === null ? "" : `seed ${money(seed, 0)}`),
      kpi("Open", int((S.positions || {}).open_count), num((S.positions || {}).open_count) ? "good" : "mute", "positions"),
    ].join("");

    const keys = Object.keys(balances);
    const vals = keys.map((k) => num(balances[k]) || 0);
    const span = Math.max.apply(null, [1e-9].concat(vals));
    $("venue-bars").innerHTML = keys.map((venue) => {
      const n = num(balances[venue]) || 0;
      return `<div class="venue"><span class="v-name">${esc(venue)}</span>` +
        `<span class="v-track"><span class="v-fill" style="width:${((n / span) * 100).toFixed(1)}%"></span></span>` +
        `<span class="v-val">${money(n)}</span></div>`;
    }).join("");

    const f = (S.positions || {}).file || {};
    $("wallet-src").textContent = `source: /api/positions · ${f.exists ? `state file ${age(f.age_seconds)} old` : "state file missing"}`;
  }

  /* ══════════ decision funnel ══════════ */
  /* Collapse "scalp_veto:quiet(atr=0.51%)" -> "scalp_veto:quiet" so one gate is
   * not spread across dozens of rows by an embedded measurement. This is
   * display grouping of returned counts, not a recomputed statistic. */
  function reasonFamily(reason) {
    return String(reason || "—")
      .replace(/\s*\([^)]*\)\s*/g, "")
      .replace(/[=:]\s*[\d.+-]+%?$/, "")
      .trim() || "—";
  }

  function renderFunnel() {
    const c = S.candidates || {};
    const term = ((S.brain || {}).cascade || {}).terminal || {};
    const byDec = {};
    (c.by_decision || []).forEach((d) => { byDec[String(d.decision).toUpperCase()] = num(d.count) || 0; });
    const total = num(c.total_in_window) || ((byDec.SKIP || 0) + (byDec.ALLOW || 0));
    const span = Math.max(1, total);

    const stages = [
      { kind: "scored", label: "Scored",  note: "candidates evaluated", v: total },
      { kind: "skip",   label: "Skipped", note: "vetoed at a gate",     v: byDec.SKIP || 0 },
      { kind: "allow",  label: "Allowed", note: "cleared the scorer",   v: byDec.ALLOW || 0 },
      { kind: "term",   label: "Filled",  note: "reached a position",   v: num(term.filled) || 0 },
    ];

    $("funnel-stages").innerHTML = stages.map((s) => {
      const w = ((s.v / span) * 100).toFixed(2);
      const share = total ? `${((s.v / total) * 100).toFixed(s.v / total < 0.01 ? 2 : 1)}%` : "—";
      return `<div class="fstage" data-kind="${s.kind}">` +
        `<span class="fs-label">${esc(s.label)}<small>${esc(s.note)}</small></span>` +
        `<span class="fs-bar"><span class="fs-fill" style="width:${w}%"></span>` +
        `<span class="fs-text"><b>${int(s.v)}</b><span class="fs-pct">${share}</span></span></span></div>`;
    }).join("");

    const grouped = new Map();
    (c.top_skip_reasons || []).forEach((r) => {
      const fam = reasonFamily(r.reason);
      grouped.set(fam, (grouped.get(fam) || 0) + (num(r.count) || 0));
    });
    const rows = Array.from(grouped.entries()).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const top = rows.length ? rows[0][1] : 1;
    $("skip-reasons").innerHTML = rows.length
      ? rows.map((row) =>
          `<div class="reason"><span class="r-label" title="${esc(row[0])}">${esc(row[0])}</span>` +
          `<span class="r-track"><span class="r-fill" style="width:${((row[1] / top) * 100).toFixed(1)}%"></span></span>` +
          `<span class="r-count">${int(row[1])}</span></div>`).join("")
      : `<p class="src">No skip reasons in this window.</p>`;

    $("funnel-window").textContent = candHours >= 168 ? "7 d" : `${candHours} h`;
    const f = c.file || {};
    $("funnel-src").textContent = c.available === false
      ? `unavailable: ${esc(c.reason || "no data")}`
      : `source: /api/candidates since ${esc(c.since_utc || "—")}` +
        (c.distinct_skip_reasons != null ? ` · ${int(c.distinct_skip_reasons)} distinct reasons grouped into ${rows.length} families` : "") +
        (f.exists ? ` · warehouse ${age(f.age_seconds)} old` : "");
  }

  /* ══════════ engine telemetry ══════════ */
  function renderTelemetry() {
    const cascade = (S.brain || {}).cascade || {};
    const live = cascade.live || {};
    const mon = live.monitor || {};
    const term = cascade.terminal || {};

    $("live-age").textContent = (live.file && live.file.age_seconds != null)
      ? `log ${age(live.file.age_seconds)} old` : "—";

    $("live-kpis").innerHTML = [
      kpi("Cycles", int(live.cycles), "mute", "engine loops"),
      kpi("Intents", int((live.intents || []).length), "mute", "entry attempts"),
      kpi("Monitored", int(mon.decisions), "mute", `${int(mon.hold)} hold`),
    ].join("");

    $("terminal-kpis").innerHTML = [
      kpi("Filled", int(term.filled), num(term.filled) ? "good" : "mute"),
      kpi("Deferred", int(term.deferred), "mute"),
      kpi("Blocked", int(term.blocked), num(term.blocked) ? "bad" : "mute"),
      kpi("Residual", int(term.residual), "mute"),
    ].join("");

    $("live-src").textContent = cascade.available === false
      ? `unavailable: ${esc(cascade.reason || "—")}`
      : `source: /api/brain cascade · ${int(cascade.window_minutes)} min window · ${int(term.tail_rows)} log rows joined`;
  }

  /* ══════════ binding constraint ══════════ */
  function renderBinding() {
    const sc = (((S.brain || {}).cascade || {}).binding || {}).scorer;
    if (!sc) {
      $("binding-sub").textContent = "—";
      $("binding-share").textContent = "—";
      $("binding-plain").textContent = "No binding constraint reported for this window.";
      $("binding-conds").innerHTML = "";
      $("binding-meas").innerHTML = "";
      return;
    }
    $("binding-sub").textContent = sc.family || "—";
    const share = $("binding-share");
    share.textContent = `${int(sc.count)} candidates · ${pct(sc.share_of_stage, 1)} of stage`;
    share.dataset.tone = "warn";
    $("binding-plain").textContent = sc.plain || "";

    const split = sc.required_split || {};
    const conds = Array.isArray(split.conditions) ? split.conditions.slice() : [];
    conds.sort((a, b) => (num(b.fail_share) || 0) - (num(a.fail_share) || 0));
    const topKey = sc.top_failure ? sc.top_failure.key : (conds[0] || {}).key;

    $("binding-conds").innerHTML = conds.length ? conds.map((cd) => {
      const passes = num(cd.passes) || 0, failures = num(cd.failures) || 0;
      const tot = (passes + failures) || 1;
      return `<div class="cond" data-top="${String(cd.key === topKey)}">` +
        `<span class="c-label" title="${esc(cd.label || cd.key)}">${esc(cd.label || cd.key)}</span>` +
        `<span class="c-track"><span class="c-pass" style="width:${((passes / tot) * 100).toFixed(1)}%"></span>` +
        `<span class="c-fail" style="width:${((failures / tot) * 100).toFixed(1)}%"></span></span>` +
        `<span class="c-pct">${pct(cd.fail_share, 1)} fail</span></div>`;
    }).join("") : `<p class="src">${esc(split.note || "Per-condition split not derivable for this family.")}</p>`;

    if (num(split.of) !== null) {
      const rm = sc.required_met || {};
      $("meas-note").textContent =
        `needs ${int(split.of)} of ${int(split.of)} · typically met ${num(rm.median) === null ? "—" : rm.median}`;
    }

    const meas = Array.isArray(sc.measurements) ? sc.measurements : [];
    $("binding-meas").innerHTML = meas.map((m) => {
      const lo = num(m.min), md = num(m.median), hi = num(m.max);
      if (lo === null || hi === null) return "";
      const spanV = (hi - lo) || 1;
      const dot = md === null ? 50 : ((md - lo) / spanV) * 100;
      const u = m.unit || "";
      return `<div class="mrow"><span class="m-label">${esc(m.label || m.key)}</span>` +
        `<span class="m-range"><span class="m-bar" style="left:0;right:0"></span>` +
        `<span class="m-dot" style="left:${dot.toFixed(1)}%"></span></span>` +
        `<span class="m-vals">${esc(lo)}${esc(u)} · <b>${md === null ? "—" : esc(md)}${esc(u)}</b> · ${esc(hi)}${esc(u)} (n=${int(m.n)})</span></div>`;
    }).join("");

    $("binding-src").textContent = sc.measurements_are_passing_values
      ? "ranges are the values of conditions that PASSED - not the failing ones · source: /api/brain"
      : "source: /api/brain cascade.binding";
  }

  /* ══════════ live decision feed ══════════ */
  function renderFeed() {
    const rows = ((S.candidates || {}).recent || []).slice(0, 24);
    const ul = $("feed");
    if (!rows.length) {
      ul.innerHTML = `<li class="src">No decisions in this window.</li>`;
      return;
    }
    ul.innerHTML = rows.map((r) => {
      const id = `${r.ts_utc}|${r.exchange}|${r.symbol}|${r.decision}`;
      const isNew = !firstFeedRender && !seenFeed.has(id);
      seenFeed.add(id);
      const dec = String(r.decision || "").toUpperCase();
      const why = dec === "ALLOW"
        ? (r.confidence != null ? `conf ${Number(r.confidence).toFixed(2)}` : "allowed")
        : reasonFamily(r.skip_reason);
      return `<li class="fitem${isNew ? " flash" : ""}" data-dec="${esc(dec)}">` +
        `<span class="f-time">${hhmm(r.ts_utc)}</span>` +
        `<span class="f-sym">${esc(r.symbol || "—")} <small>${esc(r.side || "")} ${esc(r.exchange || "")}</small></span>` +
        `<span class="f-why" title="${esc(r.skip_reason || why)}">${esc(why)}</span></li>`;
    }).join("");
    firstFeedRender = false;
    if (seenFeed.size > 600) seenFeed = new Set(Array.from(seenFeed).slice(-300));
    $("feed-count").textContent = `${rows.length} shown`;
  }

  /* ══════════ positions ══════════ */
  function renderPositions() {
    const p = S.positions || {};
    const open = p.open || [];
    $("pos-count").textContent = `${int(p.open_count)} open · ${int(p.closed_count)} closed`;
    $("pos-src-pill").textContent = p.closed_is_capped
      ? `closed list capped at ${int(p.closed_ring_cap)}` : "full history";

    $("pos-tiles").innerHTML = open.length ? open.map(positionTile).join("") : zeroState();

    const closed = (p.closed || []).slice().sort((a, b) =>
      String(b.close_time || "").localeCompare(String(a.close_time || ""))).slice(0, 12);
    $("closed-table").querySelector("tbody").innerHTML = closed.map((t) => {
      const net = num(t.pnl);
      const cls = net === null ? "" : net >= 0 ? "pos" : "neg";
      const pp = num(t.pnl_pct);
      return `<tr><td>${hhmm(t.close_time)}</td><td>${esc(t.symbol)}</td>` +
        `<td>${esc(String(t.side || "").toUpperCase())}</td>` +
        `<td class="r ${cls}">${money(net)}</td>` +
        `<td class="r ${cls}">${pp === null ? "—" : pp.toFixed(2) + "%"}</td>` +
        `<td class="r">${money(t.total_fees)}</td>` +
        `<td>${esc(t.close_reason || "—")}</td><td>${esc(t.exchange || "—")}</td></tr>`;
    }).join("");

    const cs = p.closed_stats || {};
    $("pos-src").textContent =
      `closed ring: ${int(cs.wins)}W / ${int(cs.losses)}L, net ${money(cs.net_pnl)} — ` +
      `descriptive only; the canonical windowed win rate is the gauge above (/api/goals).`;
  }

  function positionTile(pos) {
    const entry = num(pos.entry_price), sl = num(pos.stop_loss), tp = num(pos.take_profit);
    // NO LIVE PRICE EXISTS. positions.json carries no current_price /
    // unrealized_pnl, and heartbeat.json publishes only an open-position COUNT.
    // This previously fell back to entry and rendered a marker that looked
    // live but was frozen at an entry-derived position forever, with PnL as a
    // bare "-". A read-only console must not imply data it does not have:
    // the marker is now explicitly labelled "entry", which is honest AND more
    // useful -- where entry sits between the stop and target IS the R:R.
    const net = num(pos.pnl);
    const cls = net === null ? "" : net >= 0 ? "pos" : "neg";
    let mark = null;
    if (sl !== null && tp !== null && entry !== null && tp !== sl) {
      mark = Math.max(0, Math.min(100, ((entry - sl) / (tp - sl)) * 100));
    }
    // Planned reward:risk -- the number that decides whether a bracket can pay.
    const rr = (entry !== null && sl !== null && tp !== null && entry !== sl)
      ? Math.abs(tp - entry) / Math.abs(entry - sl) : null;
    return `<div class="tile"><div class="t-head">` +
      `<span class="t-sym">${esc(pos.symbol || "—")} <small class="dim">${esc(String(pos.side || "").toUpperCase())}</small></span>` +
      `<span class="t-pnl ${cls}">${net === null
        ? `<small class="dim">live PnL not published</small>` : money(net)}</span></div>` +
      `<div class="t-meta">${esc(pos.exchange || "")} · ${esc(pos.strategy || "")} · entry ${entry === null ? "—" : esc(entry)}` +
      (rr !== null
        ? ` · R:R <b class="${rr >= 1 ? "pos" : "neg"}">${rr.toFixed(2)}</b>` +
          ` (needs ${(100 / (1 + rr)).toFixed(0)}% WR)`
        : "") + `</div>` +
      `<div class="t-track"><span class="t-zone-sl" style="width:18%"></span>` +
      `<span class="t-zone-tp" style="width:18%"></span>` +
      (mark !== null
        ? `<span class="t-mark" style="left:${mark.toFixed(1)}%" title="entry (no live price available)"></span>`
        : "") + `</div>` +
      `<div class="t-ends"><span>SL ${sl === null ? "—" : esc(sl)}</span>` +
      `<span class="dim">${mark !== null ? "▲ entry" : ""}</span>` +
      `<span>TP ${tp === null ? "—" : esc(tp)}</span></div></div>`;
  }

  /* The empty deck is the NORMAL state - tens of thousands of candidates a day,
   * a handful of fills. Explain the gate rather than showing a placeholder. */
  function zeroState() {
    if (authFailed || (ERR.positions instanceof AuthError)) {
      return `<div class="empty"><h4>Locked — unlock to see the book</h4>` +
        `<p>APIs returned unauthorized. This is not a flat book — enter a valid ` +
        `<code>MISSION_CONTROL_TOKEN</code> to load positions.</p></div>`;
    }
    if (ERR.positions && !S.positions) {
      return `<div class="empty"><h4>Book unavailable</h4>` +
        `<p>Positions feed failed (${esc(ERR.positions.message || ERR.positions)}). ` +
        `Not a confirmed flat book.</p></div>`;
    }
    const c = S.candidates || {};
    const byDec = {};
    (c.by_decision || []).forEach((d) => { byDec[String(d.decision).toUpperCase()] = num(d.count) || 0; });
    const sc = (((S.brain || {}).cascade || {}).binding || {}).scorer;
    const top = sc && sc.top_failure;
    const hours = candHours >= 168 ? "7 days" : `${candHours} hours`;
    return `<div class="empty"><h4>Flat — no open positions</h4>` +
      `<p>This is the designed resting state, not a fault. In the last ${esc(hours)} the engine scored ` +
      `<b>${int(c.total_in_window)}</b> candidates and allowed <b>${int(byDec.ALLOW)}</b>; the economic gate and ` +
      `entry conditions removed the rest.</p>` +
      (sc ? `<p class="why">Nearest miss: ${esc(sc.plain || sc.family)}` +
        (top ? ` — <b>${esc(top.label || top.key)}</b> failed on ${int(top.failures)} of ${int((top.passes || 0) + (top.failures || 0))} checks (${pct(top.fail_share, 0)}).` : ".") +
        `</p>` : "") +
      `<p class="src">Loosening these gates raises trade count, not expectancy — the lane above is the honest scoreboard.</p></div>`;
  }

  /* ══════════ evidence lanes ══════════ */
  function renderLanes() {
    const f = S.funnel || {};
    const lanes = Array.isArray(f.lanes) ? f.lanes : [];
    $("lanes-age").textContent = f.generated_utc ? `built ${hhmm(f.generated_utc)}Z` : "—";
    $("lane-list").innerHTML = lanes.length ? lanes.map((l) => {
      const prog = String(l.floor_progress || "");
      const m = prog.match(/^(\d+)\s*\/\s*(\d+)$/);
      const frac = m ? Math.min(1, Number(m[1]) / Math.max(1, Number(m[2]))) : 0;
      const state = String(l.state || "—");
      const toneName = /GATE_BLOCKED|STARVED/i.test(state) ? "bad" : /ACCRU|OPEN/i.test(state) ? "good" : "";
      return `<div class="lane"><span class="l-name">${esc(l.lane)}` +
        `<small>${esc(prog || "—")} resolved${l.eta_days != null ? ` · eta ${esc(l.eta_days)}d` : ""}</small></span>` +
        `<span class="l-track"><span class="l-fill" style="width:${(frac * 100).toFixed(1)}%"></span></span>` +
        `<span class="l-state"><span class="pill" data-tone="${toneName}">${esc(state)}</span></span></div>`;
    }).join("") : `<p class="src">No lanes reported.</p>`;
    $("lanes-src").textContent = `source: /api/funnel · resolved floor ${int(f.resolved_floor)} per lane`;
  }

  /* ══════════ risk rails ══════════ */
  function renderRisk() {
    const r = S.risk || {};
    const rs = r.risk_state || {};
    const halt = $("risk-halt");
    halt.textContent = rs.is_halted ? "HALTED" : "trading";
    halt.dataset.tone = rs.is_halted ? "bad" : "good";

    const dd = num(r.max_drawdown_percent);
    // Trailing consecutive losses, counted from the newest end.
    const gs = Array.isArray(rs.global_streak) ? rs.global_streak : null;
    let lossStreak = null;
    if (gs) {
      lossStreak = 0;
      for (let i = gs.length - 1; i >= 0 && !gs[i]; i--) lossStreak++;
    }
    $("risk-kpis").innerHTML = [
      kpi("Daily PnL", money(rs.daily_pnl), tone(rs.daily_pnl)),
      kpi("Trades today", int(rs.trades_today), "mute"),
      kpi("Drawdown cap", dd === null ? "—" : `${dd.toFixed(1)}%`, "mute"),
      // global_streak is an ARRAY of last-20 outcomes (risk_manager.py:1313
      // appends is_win, so true=win/false=loss), not a scalar. Reading it as a
      // number gave num(array)=null, so this KPI rendered "-" on every poll and
      // the >2 "bad" tone could never fire -- it was hiding a 20-loss streak.
      kpi("Loss streak", lossStreak === null ? "—" : int(lossStreak),
        (lossStreak || 0) > 2 ? "bad" : "mute",
        gs ? `of last ${gs.length}` : ""),
    ].join("");

    const recent = Array.isArray(rs.recent_results) ? rs.recent_results.slice(-24) : [];
    $("risk-streak").innerHTML = recent.map((v) => {
      const win = v === true || v === 1 || v === "W" || (num(v) !== null && num(v) > 0);
      return `<span class="sq" data-w="${win ? 1 : 0}" title="${esc(String(v))}"></span>`;
    }).join("");

    const pauses = Object.keys(rs.symbol_pauses || {}).map((s) => `symbol ${s}`)
      .concat(Object.keys(rs.family_pauses || {}).map((s) => `family ${s}`));
    $("risk-pauses").innerHTML = pauses.length
      ? pauses.map((p) => `<span class="pause-chip">${esc(p)}</span>`).join("")
      : `<span class="src">no active quarantines</span>`;

    const f = r.file || {};
    $("risk-src").textContent = `source: /api/risk${f.exists ? ` · state ${age(f.age_seconds)} old` : ""}` +
      (r.incident_latch_present ? " · INCIDENT LATCH PRESENT" : "");
  }

  /* ══════════ command palette ══════════ */
  const COMMANDS = [
    { name: "Win rate: today", hint: "day", run: () => setWrLane("paper_futures_current_utc_day") },
    { name: "Win rate: 7 days", hint: "7d", run: () => setWrLane("paper_futures_7d") },
    { name: "Win rate: 30 days", hint: "30d", run: () => setWrLane("paper_futures_30d") },
    { name: "Funnel window: 6 hours", hint: "6h", run: () => setHours(6) },
    { name: "Funnel window: 24 hours", hint: "24h", run: () => setHours(24) },
    { name: "Funnel window: 7 days", hint: "7d", run: () => setHours(168) },
    { name: "Go to: decision funnel", hint: "jump", run: () => jump("p-funnel") },
    { name: "Go to: binding constraint", hint: "jump", run: () => jump("p-binding") },
    { name: "Go to: positions", hint: "jump", run: () => jump("p-pos") },
    { name: "Go to: risk rails", hint: "jump", run: () => jump("p-risk") },
    { name: "Go to: evidence lanes", hint: "jump", run: () => jump("p-lanes") },
    { name: "Open operator console", hint: "/classic", run: () => { window.location.href = "/classic"; } },
    { name: "Clear incident (operator)", hint: "ops", run: () => { window.location.href = "/classic?ops=clear-incident"; } },
    { name: "Restart bot (operator)", hint: "ops", run: () => { window.location.href = "/classic?ops=restart"; } },
    { name: "Refresh all feeds now", hint: "R", run: () => refreshAll() },
    { name: "Pause / resume polling", hint: "P", run: () => togglePause() },
    { name: "Toggle animation", hint: "motion", run: () => toggleMotion() },
    { name: "Re-enter Mission Control token", hint: "auth", run: () => askToken() },
  ];

  let palIdx = 0;
  let palRows = COMMANDS.slice();

  function openPalette() {
    $("palette").hidden = false;
    const input = $("palette-input");
    input.value = "";
    filterPalette("");
    input.focus();
  }
  function closePalette() { $("palette").hidden = true; }

  function filterPalette(q) {
    const needle = String(q || "").trim().toLowerCase();
    palRows = COMMANDS.filter((c) => !needle || c.name.toLowerCase().indexOf(needle) !== -1);
    palIdx = 0;
    drawPalette();
  }
  function drawPalette() {
    $("palette-list").innerHTML = palRows.map((c, i) =>
      `<li role="option" aria-selected="${i === palIdx}" data-i="${i}">` +
      `<span>${esc(c.name)}</span><span class="pl-hint">${esc(c.hint)}</span></li>`).join("");
  }
  function runPalette(i) {
    const cmd = palRows[i];
    closePalette();
    if (cmd) cmd.run();
  }

  function jump(id) {
    const el = $(id);
    if (!el) return;
    el.scrollIntoView({ behavior: prefersMotion() ? "smooth" : "auto", block: "center" });
    if (el.animate) {
      el.animate([
        { boxShadow: "0 0 0 0 rgba(75,214,255,0.0)" },
        { boxShadow: "0 0 0 3px rgba(75,214,255,0.45)" },
        { boxShadow: "0 0 0 0 rgba(75,214,255,0.0)" },
      ], { duration: 1200 });
    }
  }
  const prefersMotion = () => document.documentElement.dataset.motion === "on" &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ══════════ controls ══════════ */
  function setWrLane(lane) {
    wrLane = lane;
    document.querySelectorAll("#p-wr .seg button").forEach((b) =>
      b.classList.toggle("on", b.dataset.win === lane));
    scheduleRender();
  }
  function setHours(h) {
    candHours = h;
    document.querySelectorAll("#p-funnel .seg button").forEach((b) =>
      b.classList.toggle("on", Number(b.dataset.hours) === h));
    firstFeedRender = true;
    seenFeed = new Set();
    pull("candidates");
  }
  function togglePause() {
    paused = !paused;
    const b = $("btn-pause");
    b.setAttribute("aria-pressed", String(paused));
    b.textContent = paused ? "Resume" : "Pause";
    if (!paused) refreshAll();
    scheduleRender();
  }
  function toggleMotion() {
    const on = document.documentElement.dataset.motion === "on";
    document.documentElement.dataset.motion = on ? "off" : "on";
    try { localStorage.setItem("deck_motion", on ? "off" : "on"); } catch (e) { /* ignore */ }
    toast(on ? "Animation off" : "Animation on");
  }
  function refreshAll() {
    const keys = TIERS.reduce((acc, t) => acc.concat(t.keys), []);
    const saved = paused;
    paused = false;
    return pullAll(keys).finally(() => { paused = saved; });
  }

  /* ══════════ boot ══════════ */
  function boot() {
    try {
      if (localStorage.getItem("deck_motion") === "off") document.documentElement.dataset.motion = "off";
    } catch (e) { /* ignore */ }

    $("btn-pause").addEventListener("click", togglePause);
    $("btn-refresh").addEventListener("click", refreshAll);
    $("btn-palette").addEventListener("click", openPalette);

    document.querySelectorAll("#p-wr .seg button").forEach((b) =>
      b.addEventListener("click", () => setWrLane(b.dataset.win)));
    document.querySelectorAll("#p-funnel .seg button").forEach((b) =>
      b.addEventListener("click", () => setHours(Number(b.dataset.hours))));

    $("palette-input").addEventListener("input", (e) => filterPalette(e.target.value));
    $("palette-list").addEventListener("click", (e) => {
      const li = e.target.closest("li[data-i]");
      if (li) runPalette(Number(li.dataset.i));
    });
    $("palette").addEventListener("click", (e) => { if (e.target.id === "palette") closePalette(); });

    document.addEventListener("keydown", (e) => {
      const open = !$("palette").hidden;
      if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === "k") {
        e.preventDefault();
        if (open) closePalette(); else openPalette();
        return;
      }
      if (open) {
        if (e.key === "Escape") { e.preventDefault(); closePalette(); }
        else if (e.key === "ArrowDown") { e.preventDefault(); palIdx = Math.min(palRows.length - 1, palIdx + 1); drawPalette(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); palIdx = Math.max(0, palIdx - 1); drawPalette(); }
        else if (e.key === "Enter") { e.preventDefault(); runPalette(palIdx); }
        return;
      }
      if ($("token-dialog").open) return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      if (e.key === "p" || e.key === "P") togglePause();
      else if (e.key === "r" || e.key === "R") refreshAll();
      else if (e.key === "?") openPalette();
    });

    $("token-form").addEventListener("submit", (e) => {
      const ok = e.submitter && e.submitter.value === "ok";
      const val = $("token-input").value.trim();
      if (ok && val) {
        setToken(val);
        authFailed = false;
        Object.keys(ERR).forEach((k) => { ERR[k] = null; });
        toast("Token saved — retrying feeds", "good");
        setTimeout(refreshAll, 60);
      }
    });

    /* Stop polling when the tab is hidden; this box also runs the bot, the MCP
     * servers, the harvesters and a Codex loop. */
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !paused) refreshAll();
      scheduleRender();
    });

    setInterval(() => {
      $("clock").textContent = new Date().toISOString().slice(11, 19) + "Z";
    }, 1000);

    TIERS.forEach((tier) => {
      pullAll(tier.keys);
      setInterval(() => pullAll(tier.keys), tier.ms);
    });

    scheduleRender();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
