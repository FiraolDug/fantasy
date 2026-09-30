(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }

  const view = document.getElementById("view");
  const alertBox = document.getElementById("alert");
  const stepsEl = document.getElementById("steps");
  let token = null, state = null, poll = null, tick = null, ref = null;

  // ---------- helpers ----------
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) n.append(kid instanceof Node ? kid : document.createTextNode(kid));
    return n;
  };
  const showError = (msg) => { alertBox.textContent = msg; alertBox.hidden = !msg; if (msg) alertBox.scrollIntoView({ block: "nearest" }); };

  async function api(path, body) {
    const res = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const err = new Error(typeof d === "string" ? d : (d && d.message) || "Something went wrong. Try again.");
      err.code = d && d.code; err.attemptsLeft = d && d.attempts_left; err.status = res.status;
      throw err;
    }
    return data;
  }

  async function run(button, fn) {
    showError("");
    button.disabled = true;
    try { await fn(); }
    catch (e) {
      const extra = typeof e.attemptsLeft === "number" && e.attemptsLeft <= 3 ? ` You have ${e.attemptsLeft} ${e.attemptsLeft === 1 ? "attempt" : "attempts"} left today.` : "";
      showError(e.message + (e.code === "locked" ? "" : extra));
    }
    finally { button.disabled = false; }
  }

  function paintSteps(step) {
    const order = ["phone", "manager_id", "team_name", "ownership"];
    const idx = step === "done" ? order.length : order.indexOf(step);
    [...stepsEl.children].forEach((li, i) => {
      li.className = i < idx ? "done" : i === idx ? "current" : "";
      if (i === idx) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
  }

  async function refresh() {
    state = await api("/verification/status");
    render();
  }

  // ---------- screens ----------
  function render() {
    clearInterval(poll); clearInterval(tick);
    showError("");
    paintSteps(state.step);
    view.replaceChildren(({ phone, manager_id: managerId, team_name: teamName, ownership, done })[state.step]());
    const first = view.querySelector("input");
    if (first) first.focus({ preventScroll: true });
  }

  function phone() {
    const canRequest = tg && tg.isVersionAtLeast && tg.isVersionAtLeast("6.9") && tg.requestContact;
    const status = el("p", { class: "muted small", role: "status" });
    const btn = el("button", { class: "btn", type: "button", onclick: () => {
      if (!canRequest) return;
      run(btn, async () => {
        await new Promise((resolve) => tg.requestContact(resolve));
        status.textContent = "Waiting for Telegram to confirm your number…";
        let tries = 0;
        poll = setInterval(async () => {
          tries++;
          try { const s = await api("/verification/status"); if (s.phone_verified) { state = s; render(); } } catch (_) {}
          if (tries > 40) { clearInterval(poll); status.textContent = "Still nothing. Open the bot chat and tap “Share my phone number”."; }
        }, 2500);
      });
    } }, "Share my phone number");
    if (!canRequest) btn.disabled = true;
    return el("div", {},
      el("h1", {}, "Confirm your phone number"),
      el("p", { class: "muted" }, "Telegram confirms the number belongs to your account. We never see it unless you share it, and it can’t be changed later without support."),
      canRequest ? btn : el("div", {}, el("div", { class: "notice warn" }, "Your Telegram app can’t share a number from here. Open the bot chat and tap “Share my phone number”, then come back."), el("button", { class: "btn secondary", type: "button", onclick: () => run(btn, refresh) }, "I’ve shared it")),
      status);
  }

  function managerId() {
    const input = el("input", { class: "input", id: "mid", inputmode: "numeric", autocomplete: "off", maxlength: "10", pattern: "[0-9]*", "aria-describedby": "mid-hint" });
    const btn = el("button", { class: "btn", type: "submit" }, "Continue");
    const refInput = ref && ref.can_apply_code ? el("input", { class: "input", id: "rc", autocomplete: "off", maxlength: "12", spellcheck: "false", style: false }) : null;
    const form = el("form", { novalidate: true, onsubmit: (e) => {
      e.preventDefault();
      const v = input.value.trim();
      input.setAttribute("aria-invalid", /^[1-9][0-9]{0,9}$/.test(v) ? "false" : "true");
      if (!/^[1-9][0-9]{0,9}$/.test(v)) return showError("A Manager ID is a number with up to 10 digits.");
      run(btn, async () => {
        if (refInput && refInput.value.trim()) { await api("/referrals/apply", { code: refInput.value.trim() }); ref = { ...ref, can_apply_code: false }; }
        await api("/verification/fpl/manager-id", { manager_id: v });
        await refresh();
      });
    } },
      el("div", { class: "field" }, el("label", { for: "mid" }, "FPL Manager ID"), input,
        el("p", { class: "hint", id: "mid-hint" }, "On the FPL website, open Points. The number in the address bar after “entry/” is your Manager ID.")),
      refInput ? el("div", { class: "field" }, el("label", { for: "rc" }, "Referral code (optional)"), refInput, el("p", { class: "hint" }, "Got an invite from a friend? Enter their code.")) : null,
      btn);
    return el("div", {}, el("h1", {}, "Your FPL Manager ID"),
      el("p", { class: "muted" }, "Only Ethiopian FPL accounts can join. We check the country registered on your FPL profile."), form);
  }

  function teamName() {
    const input = el("input", { class: "input", id: "tn", autocomplete: "off", maxlength: "64", spellcheck: "false", "aria-describedby": "tn-hint" });
    const btn = el("button", { class: "btn", type: "submit" }, "Verify team name");
    const form = el("form", { novalidate: true, onsubmit: (e) => {
      e.preventDefault();
      if (!input.value.trim()) { input.setAttribute("aria-invalid", "true"); return showError("Enter your team name."); }
      input.setAttribute("aria-invalid", "false");
      run(btn, async () => {
        try { await api("/verification/fpl/team-name", { team_name: input.value }); await refresh(); }
        catch (err) { if (err.code === "name_mismatch") input.setAttribute("aria-invalid", "true"); throw err; }
      });
    } },
      el("div", { class: "notice ok" }, "Country check passed: Ethiopia."),
      el("div", { class: "field" }, el("label", { for: "tn" }, "FPL team name"), input,
        el("p", { class: "hint", id: "tn-hint" }, "Type it exactly as it appears in FPL: same spelling, capital letters, spaces and emoji.")),
      btn,
      el("button", { class: "btn secondary", type: "button", onclick: () => { state = { ...state, step: "manager_id" }; render(); } }, "Use a different Manager ID"));
    return el("div", {}, el("h1", {}, "Your FPL team name"),
      el("p", { class: "muted" }, "The name must match the Manager ID you entered. If it doesn’t, verification is blocked."), form);
  }

  function ownership() {
    const c = state.challenge;
    const left = el("span", {});
    const paint = () => { const s = Math.max(0, Math.round((new Date(c.expires_at) - Date.now()) / 1000)); left.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; if (s === 0) { clearInterval(tick); refresh(); } };
    paint(); tick = setInterval(paint, 1000);
    const copy = el("button", { class: "btn secondary", type: "button", onclick: async () => { try { await navigator.clipboard.writeText(c.code); copy.textContent = "Copied"; } catch (_) { copy.textContent = "Select and copy"; } } }, "Copy");
    const btn = el("button", { class: "btn", type: "button", onclick: () => run(btn, async () => { await api("/verification/fpl/ownership", {}); await refresh(); }) }, "Check my team name");
    return el("div", {}, el("h1", {}, "Prove this is your team"),
      el("p", { class: "muted" }, "Your ID and team name are visible to anyone in FPL, so we also need to see that you can log in to this account."),
      el("ol", { class: "how" },
        el("li", {}, "Open the FPL website or app and go to Pick Team → Team details."),
        el("li", {}, "Change your team name to this code and save:")),
      el("div", { class: "code" }, el("output", {}, c.code), copy),
      el("ol", { class: "how", start: "3" },
        el("li", {}, "Come back here and check. You can change your team name back straight after.")),
      btn, el("p", { class: "hint center" }, "Code expires in ", left));
  }

  function done() {
    const t = state.team;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 56 56"); svg.setAttribute("class", "tick"); svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = '<circle cx="28" cy="28" r="26" fill="#e7f2eb" stroke="#1c7a4b" stroke-width="2"/><path d="M17 29l8 8 14-16" fill="none" stroke="#1c7a4b" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>';
    return el("div", { class: "center" }, svg, el("h1", {}, "Team verified"),
      el("p", { class: "muted" }, "This FPL team is now linked to your account."),
      el("dl", { class: "summary" },
        el("div", {}, el("dt", {}, "Team"), el("dd", {}, t.team_name)),
        el("div", {}, el("dt", {}, "Manager ID"), el("dd", {}, t.manager_id)),
        el("div", {}, el("dt", {}, "Country"), el("dd", {}, "Ethiopia"))),
      el("a", { class: "btn", href: "index.html", style: false }, "Continue"));
  }

  // ---------- start ----------
  (async () => {
    try {
      if (!tg || !tg.initData) throw new Error("Open this page from the Telegram bot to sign in.");
      const t = await api("/auth/telegram-webapp", { init_data: tg.initData });
      token = t.access_token;
      const start = tg.initDataUnsafe && tg.initDataUnsafe.start_param;
      if (start && /^[A-Za-z0-9]{4,12}$/.test(start)) { try { await api("/referrals/apply", { code: start }); } catch (_) { /* invalid or already used: ignore */ } }
      try { ref = await api("/referrals/me"); } catch (_) { ref = null; }
      await refresh();
    } catch (e) {
      view.replaceChildren(el("h1", {}, "Can’t open verification"), el("p", { class: "muted" }, e.message));
    }
  })();
})();
