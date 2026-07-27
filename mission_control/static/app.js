(() => {
  const TOKEN_KEY = "mission_control_token";

  const $ = (id) => document.getElementById(id);
  const pretty = (obj) => JSON.stringify(obj, null, 2);

  function getToken() {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  }

  function setToken(token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  }

  async function api(path, options = {}) {
    const headers = Object.assign(
      { Accept: "application/json" },
      options.headers || {}
    );
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    if (options.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, { ...options, headers });
    if (res.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      await ensureToken(true);
      throw new Error("unauthorized — re-enter token");
    }
    const text = await res.text();
    let data;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { raw: text };
    }
    if (!res.ok) {
      throw new Error(data.error || data.detail || res.statusText || "request failed");
    }
    return data;
  }

  function ensureToken(force = false) {
    if (!force && getToken()) return Promise.resolve();
    const dialog = $("token-dialog");
    const input = $("token-input");
    input.value = "";
    dialog.showModal();
    return new Promise((resolve) => {
      $("token-form").onsubmit = (ev) => {
        if (ev.submitter && ev.submitter.value === "ok") {
          setToken(input.value.trim());
        }
        resolve();
      };
    });
  }

  function askConfirm({ title, desc, phraseRequired }) {
    const dialog = $("confirm-dialog");
    $("confirm-title").textContent = title;
    $("confirm-desc").textContent = desc || "";
    $("confirm-note").value = "";
    const phraseWrap = $("confirm-phrase-wrap");
    const phraseInput = $("confirm-phrase");
    phraseInput.value = "";
    if (phraseRequired) {
      phraseWrap.style.display = "";
      phraseInput.required = true;
      phraseInput.placeholder = phraseRequired;
    } else {
      phraseWrap.style.display = "none";
      phraseInput.required = false;
    }
    dialog.showModal();
    return new Promise((resolve) => {
      $("confirm-form").onsubmit = (ev) => {
        const value = ev.submitter ? ev.submitter.value : "cancel";
        if (value !== "ok") {
          resolve(null);
          return;
        }
        resolve({
          operator_note: $("confirm-note").value.trim(),
          confirm_phrase: phraseInput.value.trim(),
        });
      };
    });
  }

  function setStatusLine(status) {
    const age = status.heartbeat_age_seconds;
    const ageText = age == null ? "?" : `${Math.round(age)}s`;
    const halt = status.is_halted ? "HALTED" : "ok";
    const kill = status.kill_switch ? "KILL_ON" : "kill_off";
    const alive = status.supervisor && status.supervisor.alive ? "supervisor_up" : "supervisor_?";
    $("status-line").textContent =
      `${status.mode || "?"} · profile ${status.paper_profile || "?"} · ` +
      `${halt} · ${kill} · heartbeat ${ageText} · ${alive}`;
  }

  async function refresh() {
    const [status, positions, risk, funnel, goals, gates, tasks, audit] = await Promise.all([
      api("/api/status"),
      api("/api/positions"),
      api("/api/risk"),
      api("/api/funnel"),
      api("/api/goals"),
      api("/api/gates"),
      api("/api/tasks"),
      api("/api/audit?limit=40"),
    ]);
    setStatusLine(status);
    if (status.mode) $("mode-select").value = status.mode === "OBSERVATION" ? "OBSERVATION" : "PAPER";
    if (status.paper_profile) {
      const sel = $("profile-select");
      if ([...sel.options].some((o) => o.value === status.paper_profile)) {
        sel.value = status.paper_profile;
      }
    }
    $("positions-body").textContent = pretty(positions);
    $("risk-body").textContent = pretty(risk);
    $("funnel-body").textContent = pretty({ funnel, goals });
    $("gates-body").textContent = pretty(gates);
    $("tasks-body").textContent = pretty(tasks);
    const actions = $("tasks-actions");
    actions.innerHTML = "";
    const list = (tasks && tasks.tasks) || [];
    list.forEach((t) => {
      const name = t.Name || t.name;
      if (!name || t.error) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = `Run ${name}`;
      btn.addEventListener("click", () => handleOp("run-task:" + name));
      actions.appendChild(btn);
    });
    $("audit-body").textContent = pretty(audit.entries || []);
  }

  async function handleOp(op) {
    try {
      if (op === "refresh") {
        await refresh();
        return;
      }
      if (op === "restart") {
        const conf = await askConfirm({
          title: "Restart bot",
          desc: 'Type confirm phrase exactly: RESTART BOT',
          phraseRequired: "RESTART BOT",
        });
        if (!conf) return;
        await api("/api/ops/restart", {
          method: "POST",
          body: JSON.stringify(conf),
        });
      } else if (op === "kill-on" || op === "kill-off") {
        const conf = await askConfirm({
          title: op === "kill-on" ? "Enable kill switch" : "Disable kill switch",
          desc: "Stops or resumes NEW entries only.",
        });
        if (!conf) return;
        await api("/api/ops/kill-switch", {
          method: "POST",
          body: JSON.stringify({
            operator_note: conf.operator_note,
            enabled: op === "kill-on",
          }),
        });
      } else if (op === "clear-incident") {
        const conf = await askConfirm({
          title: "Clear incident latch",
          desc: "Type confirm phrase exactly: CLEAR INCIDENT",
          phraseRequired: "CLEAR INCIDENT",
        });
        if (!conf) return;
        await api("/api/ops/clear-incident", {
          method: "POST",
          body: JSON.stringify(conf),
        });
      } else if (op === "set-mode") {
        const conf = await askConfirm({
          title: "Change mode",
          desc: "Type confirm phrase exactly: CHANGE MODE",
          phraseRequired: "CHANGE MODE",
        });
        if (!conf) return;
        await api("/api/ops/mode", {
          method: "POST",
          body: JSON.stringify({
            ...conf,
            mode: $("mode-select").value,
          }),
        });
      } else if (op === "set-profile") {
        const conf = await askConfirm({
          title: "Change paper profile",
          desc: "Updates .env PAPER_TRADING_PROFILE. Restart supervisor to pick up.",
        });
        if (!conf) return;
        await api("/api/ops/paper-profile", {
          method: "POST",
          body: JSON.stringify({
            operator_note: conf.operator_note,
            profile: $("profile-select").value,
          }),
        });
      } else if (op === "run-funnel") {
        const conf = await askConfirm({ title: "Run promotion funnel" });
        if (!conf) return;
        await api("/api/ops/run-funnel", {
          method: "POST",
          body: JSON.stringify({ operator_note: conf.operator_note }),
        });
      } else if (op === "run-goals") {
        const conf = await askConfirm({ title: "Run goal report" });
        if (!conf) return;
        await api("/api/ops/run-goal-report", {
          method: "POST",
          body: JSON.stringify({ operator_note: conf.operator_note }),
        });
      } else if (op.startsWith("run-task:")) {
        const name = op.slice("run-task:".length);
        const conf = await askConfirm({ title: `Run scheduled task ${name}` });
        if (!conf) return;
        await api(`/api/ops/task/${encodeURIComponent(name)}/run`, {
          method: "POST",
          body: JSON.stringify({ operator_note: conf.operator_note }),
        });
      } else if (op.startsWith("live-")) {
        const action = op.slice(5);
        const needsPhrase = action === "arm" || action === "start";
        const conf = await askConfirm({
          title: `CONTROLLED_LIVE ${action}`,
          desc: needsPhrase
            ? "Type confirm phrase exactly: ENABLE CONTROLLED LIVE"
            : "Disarm/stop returns to PAPER.",
          phraseRequired: needsPhrase ? "ENABLE CONTROLLED LIVE" : null,
        });
        if (!conf) return;
        await api("/api/ops/controlled-live", {
          method: "POST",
          body: JSON.stringify({
            action,
            operator_note: conf.operator_note,
            confirm_phrase: conf.confirm_phrase || undefined,
          }),
        });
      }
      await refresh();
    } catch (err) {
      alert(String(err.message || err));
    }
  }

  document.body.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-op]");
    if (!btn) return;
    handleOp(btn.getAttribute("data-op"));
  });

  (async () => {
    await ensureToken(false);
    try {
      await refresh();
      setInterval(() => {
        refresh().catch(() => {});
      }, 15000);
    } catch (err) {
      $("status-line").textContent = `load failed: ${err.message || err}`;
    }
  })();
})();
