(() => {
  "use strict";
  const app = document.getElementById("app");
  let me = null, csrf = "", page = "overview", flash = null;

  // ---- tiny DOM helper: text is always inserted as text, never as HTML ----
  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v === true) n.setAttribute(k, "");
      else if (v !== false && v != null) n.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return n;
  };
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
  const can = (p) => me && me.permissions.includes(p);

  async function api(path, opts = {}) {
    const method = opts.method || "GET";
    const res = await fetch("/admin-api" + path, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && path !== "/auth/login") { me = null; draw(); throw new Error("Your session ended. Sign in again."); }
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : "Request failed");
    }
    return data;
  }
  const act = async (fn, okMsg) => { try { await fn(); flash = okMsg ? { ok: true, text: okMsg } : null; } catch (e) { flash = { ok: false, text: e.message }; } draw(); };
  const ask = (q) => { const r = prompt(q); return r && r.trim().length >= 5 ? r.trim() : null; };

  // ---- login ----
  function login() {
    const email = h("input", { class: "input", type: "email", autocomplete: "username", required: true });
    const pw = h("input", { class: "input", type: "password", autocomplete: "current-password", required: true });
    const code = h("input", { class: "input", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "6", pattern: "[0-9]{6}", required: true });
    const err = h("div", { class: "msg", hidden: true, role: "alert" });
    const f = h("form", { class: "card", onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      try { me = await api("/auth/login", { method: "POST", body: { email: email.value, password: pw.value, code: code.value } }); csrf = me.csrf_token; page = "overview"; draw(); }
      catch (x) { err.textContent = x.message; err.hidden = false; pw.value = ""; code.value = ""; }
    } },
      h("h1", {}, "Admin sign in"), err,
      h("div", { class: "field" }, h("label", {}, "Email"), email),
      h("div", { class: "field" }, h("label", {}, "Password"), pw),
      h("div", { class: "field" }, h("label", {}, "Authenticator code"), code),
      h("button", { class: "btn", type: "submit" }, "Sign in"));
    return h("div", { class: "login" }, f);
  }

  // ---- generic table ----
  const table = (cols, rows) => h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c.label)))),
    h("tbody", {}, rows.length ? rows.map((r) => h("tr", {}, cols.map((c) => h("td", {}, c.render ? c.render(r) : r[c.key] ?? "—")))) : h("tr", {}, h("td", { colspan: cols.length, class: "muted" }, "Nothing here yet.")))));
  const tag = (t, bad) => h("span", { class: "tag" + (bad ? " bad" : "") }, t);

  // ---- pages ----
  const pages = {
    async overview() {
      const o = await api("/overview");
      const labels = { users: "Users", verified_users: "Verified teams", open_fraud_alerts: "Open fraud alerts", pending_deposits: "Pending deposits", pending_withdrawals: "Pending withdrawals", draft_posts: "Draft posts", current_gameweek: "Current gameweek" };
      return [h("h1", {}, "Overview"), h("div", { class: "stats" }, Object.entries(labels).filter(([k]) => o[k] != null).map(([k, l]) => h("div", { class: "stat" }, h("b", {}, o[k]), h("span", {}, l))))];
    },

    async posts() {
      const list = await api("/posts");
      const title = h("input", { class: "input", maxlength: "160" });
      const summary = h("input", { class: "input", maxlength: "300" });
      const body = h("textarea", { class: "input", maxlength: "20000" });
      const status = h("select", {}, h("option", { value: "DRAFT" }, "Save as draft"), can("post.publish") ? h("option", { value: "PUBLISHED" }, "Publish now") : null);
      const pinned = h("input", { type: "checkbox" });
      const form = h("form", { class: "card", onsubmit: (e) => { e.preventDefault(); act(async () => api("/posts", { method: "POST", body: { title: title.value, summary: summary.value || null, body: body.value, pinned: pinned.checked, status: status.value } }), "Post saved."); } },
        h("h2", {}, "New post"),
        h("div", { class: "field" }, h("label", {}, "Title"), title),
        h("div", { class: "field" }, h("label", {}, "Summary (optional)"), summary),
        h("div", { class: "field" }, h("label", {}, "Body (plain text)"), body),
        h("div", { class: "row" }, status, h("label", {}, pinned, " Pin to top"), h("button", { class: "btn", type: "submit" }, "Save")));
      return [h("h1", {}, "Posts"), can("post.write") ? form : null, table([
        { label: "Title", key: "title" }, { label: "Status", render: (p) => tag(p.status, p.status === "ARCHIVED") }, { label: "Updated", render: (p) => fmt(p.updated_at) },
        { label: "", render: (p) => h("div", { class: "row" },
          can("post.publish") && p.status !== "PUBLISHED" ? h("button", { class: "btn secondary", onclick: () => act(() => api(`/posts/${p.id}`, { method: "PATCH", body: { status: "PUBLISHED" } }), "Published.") }, "Publish") : null,
          can("post.publish") && p.status === "PUBLISHED" ? h("button", { class: "btn secondary", onclick: () => act(() => api(`/posts/${p.id}`, { method: "PATCH", body: { status: "ARCHIVED" } }), "Archived.") }, "Archive") : null,
          can("post.delete") ? h("button", { class: "btn danger", onclick: () => confirm("Delete this post?") && act(() => api(`/posts/${p.id}`, { method: "DELETE" }), "Deleted.") }, "Delete") : null) }], list)];
    },

    async users() {
      const q = h("input", { class: "input", placeholder: "Telegram ID, Manager ID or name", value: sessionStorage.getItem("uq") || "" });
      const list = await api("/users?q=" + encodeURIComponent(q.value));
      const search = h("form", { class: "row", onsubmit: (e) => { e.preventDefault(); sessionStorage.setItem("uq", q.value); draw(); } }, q, h("button", { class: "btn secondary", type: "submit" }, "Search"));
      return [h("h1", {}, "Users"), search, table([
        { label: "Name", key: "full_name" }, { label: "Phone", key: "phone" }, { label: "FPL team", key: "team_name" }, { label: "Manager ID", key: "manager_id" },
        { label: "Status", render: (u) => tag(u.status, u.status !== "ACTIVE") },
        { label: "", render: (u) => h("div", { class: "row" },
          can("user.suspend") ? h("button", { class: "btn secondary", onclick: () => { const r = ask("Reason (min 5 characters)?"); if (r) act(() => api(`/users/${u.id}/status`, { method: "POST", body: { status: u.status === "ACTIVE" ? "SUSPENDED" : "ACTIVE", reason: r } }), "Updated."); } }, u.status === "ACTIVE" ? "Suspend" : "Reinstate") : null,
          can("verification.review") && u.manager_id ? h("button", { class: "btn danger", onclick: () => { const r = ask("Reason for unlinking this FPL team?"); if (r) act(() => api(`/users/${u.id}/unlink-team`, { method: "POST", body: { reason: r } }), "Team unlinked."); } }, "Unlink team") : null) }], list)];
    },

    async deposits() {
      const list = await api("/deposits?status=PENDING");
      return [h("h1", {}, "Pending deposits"), h("p", { class: "muted" }, "Check the transaction ID in your Telebirr or bank statement before approving."), table([
        { label: "User", key: "user" }, { label: "Method", key: "method" }, { label: "Amount (ETB)", key: "amount" }, { label: "Transaction ID", key: "transaction_id" }, { label: "Submitted", render: (d) => fmt(d.created_at) },
        { label: "", render: (d) => h("div", { class: "row" },
          h("button", { class: "btn", onclick: () => confirm(`Credit ${d.amount} ETB to ${d.user}?`) && act(() => api(`/deposits/${d.id}/approve`, { method: "POST" }), "Deposit approved.") }, "Approve"),
          h("button", { class: "btn danger", onclick: () => { const r = ask("Reason for rejecting?"); if (r) act(() => api(`/deposits/${d.id}/reject`, { method: "POST", body: { reason: r } }), "Deposit rejected."); } }, "Reject")) }], list)];
    },

    async withdrawals() {
      const list = await api("/withdrawals?status=PENDING");
      return [h("h1", {}, "Pending withdrawals"), h("p", { class: "muted" }, "Send the money first, then press Mark as paid. Reject returns the amount to the user's wallet."), table([
        { label: "User", key: "user" }, { label: "Amount (ETB)", key: "amount" }, { label: "Send to", key: "destination" }, { label: "Requested", render: (w) => fmt(w.requested_at) },
        { label: "", render: (w) => h("div", { class: "row" },
          h("button", { class: "btn", onclick: () => confirm(`Confirm ${w.amount} ETB was sent to ${w.destination}?`) && act(() => api(`/withdrawals/${w.id}/approve`, { method: "POST" }), "Marked as paid.") }, "Mark as paid"),
          h("button", { class: "btn danger", onclick: () => { const r = ask("Reason for rejecting?"); if (r) act(() => api(`/withdrawals/${w.id}/reject`, { method: "POST", body: { reason: r } }), "Rejected and refunded."); } }, "Reject")) }], list)];
    },

    async gameweeks() {
      const list = await api("/gameweeks");
      const n = h("input", { class: "input", type: "number", min: "1", max: "60" });
      const fee = h("input", { class: "input", type: "number", min: "0", step: "1", value: "200" });
      const dl = h("input", { class: "input", type: "datetime-local" });
      const f = h("form", { class: "card", onsubmit: (e) => { e.preventDefault(); act(() => api("/gameweeks", { method: "POST", body: { gw_number: +n.value, entry_fee: fee.value, registration_deadline: new Date(dl.value).toISOString() } }), "Gameweek created."); } },
        h("h2", {}, "New gameweek"), h("div", { class: "row" }, h("div", {}, h("label", {}, "Number"), n), h("div", {}, h("label", {}, "Entry fee (ETB)"), fee), h("div", {}, h("label", {}, "Registration closes"), dl), h("button", { class: "btn", type: "submit" }, "Create")));
      return [h("h1", {}, "Gameweeks"), f, table([{ label: "GW", key: "gw_number" }, { label: "Fee", key: "entry_fee" }, { label: "Status", key: "status" }, { label: "Closes", render: (g) => fmt(g.deadline) }, { label: "Entries", key: "participants" }], list)];
    },

    async referrals() {
      const list = await api("/referrals?status=HELD");
      return [h("h1", {}, "Held referral rewards"), h("p", { class: "muted" }, "These were held automatically (shared network or cap reached). Approve pays the referrer; Void cancels it."), table([
        { label: "Referrer", key: "referrer" }, { label: "Referred", key: "referee" }, { label: "Why held", key: "note" }, { label: "Created", render: (r) => fmt(r.created_at) },
        { label: "", render: (r) => h("div", { class: "row" },
          h("button", { class: "btn", onclick: () => confirm("Pay this reward?") && act(() => api(`/referrals/${r.id}/approve`, { method: "POST" }), "Reward paid.") }, "Approve"),
          h("button", { class: "btn danger", onclick: () => { const x = ask("Reason for voiding?"); if (x) act(() => api(`/referrals/${r.id}/void`, { method: "POST", body: { reason: x } }), "Voided."); } }, "Void")) }], list)];
    },

    async fraud() {
      const list = await api("/fraud-alerts");
      return [h("h1", {}, "Fraud alerts"), table([
        { label: "Severity", render: (a) => tag(a.severity, a.severity === "HIGH" || a.severity === "CRITICAL") }, { label: "Category", key: "category" }, { label: "Details", key: "description" }, { label: "Raised", render: (a) => fmt(a.created_at) },
        { label: "", render: (a) => a.resolved ? tag("Resolved") : h("button", { class: "btn secondary", onclick: () => { const r = ask("How was this resolved?"); if (r) act(() => api(`/fraud-alerts/${a.id}/resolve`, { method: "POST", body: { reason: r } }), "Resolved."); } }, "Resolve") }], list)];
    },

    async audit() {
      const [log, ev] = await Promise.all([api("/audit-log"), api("/security-events")]);
      return [h("h1", {}, "Audit log"), table([{ label: "When", render: (l) => fmt(l.at) }, { label: "Admin", key: "actor" }, { label: "Action", key: "action" }, { label: "Item", key: "entity" }, { label: "IP", key: "ip" }], log),
        h("h1", { class: "gap" }, "Security events"), table([{ label: "When", render: (e) => fmt(e.at) }, { label: "Event", key: "type" }, { label: "Severity", key: "severity" }, { label: "User", key: "user_id" }], ev)];
    },

    async admins() {
      const [list, roles] = await Promise.all([api("/admins"), api("/roles")]);
      const email = h("input", { class: "input", type: "email" }), name = h("input", { class: "input" }), pw = h("input", { class: "input", type: "password", autocomplete: "new-password" });
      const role = h("select", {}, roles.map((r) => h("option", { value: r.name }, `${r.name} — ${r.description}`)));
      const out = h("div", { class: "msg ok", hidden: true });
      const f = h("form", { class: "card", onsubmit: async (e) => { e.preventDefault(); try { const r = await api("/admins", { method: "POST", body: { email: email.value, full_name: name.value, role: role.value, password: pw.value } }); out.textContent = "Created. Give this authenticator link to the new admin now; it is not shown again: " + r.totp_uri; out.hidden = false; pw.value = ""; } catch (x) { out.textContent = x.message; out.className = "msg"; out.hidden = false; } } },
        h("h2", {}, "New admin"), out, h("div", { class: "field" }, h("label", {}, "Email"), email), h("div", { class: "field" }, h("label", {}, "Full name"), name),
        h("div", { class: "field" }, h("label", {}, "Temporary password (12+ characters)"), pw), h("div", { class: "field" }, h("label", {}, "Role"), role), h("button", { class: "btn", type: "submit" }, "Create admin"));
      return [h("h1", {}, "Admins"), f, table([{ label: "Email", key: "email" }, { label: "Name", key: "full_name" }, { label: "Role", key: "role" }, { label: "Status", render: (a) => tag(a.status, a.status !== "ACTIVE") }, { label: "Last sign-in", render: (a) => fmt(a.last_login_at) },
        { label: "", render: (a) => a.id === me.id ? h("span", { class: "muted" }, "You") : h("div", { class: "row" },
          h("button", { class: "btn secondary", onclick: () => act(() => api(`/admins/${a.id}`, { method: "PATCH", body: { status: a.status === "ACTIVE" ? "DISABLED" : "ACTIVE" } }), "Updated.") }, a.status === "ACTIVE" ? "Disable" : "Enable"),
          h("button", { class: "btn secondary", onclick: () => confirm("Reset this admin's authenticator? They will be signed out.") && act(async () => { const r = await api(`/admins/${a.id}/reset-mfa`, { method: "POST" }); alert("New authenticator link (shown once):\n" + r.totp_uri); }, "Authenticator reset.") }, "Reset MFA")) }], list)];
    },
  };

  const NAV = [["overview", "Overview", null], ["posts", "Posts", "post.read"], ["users", "Users", "user.read"], ["deposits", "Deposits", "deposit.review"], ["withdrawals", "Withdrawals", "withdrawal.review"],
    ["gameweeks", "Gameweeks", "gameweek.manage"], ["referrals", "Referrals", "referral.manage"], ["fraud", "Fraud alerts", "fraud.manage"], ["audit", "Audit log", "audit.read"], ["admins", "Admins", "admin.manage"]];

  async function draw() {
    if (!me) { app.replaceChildren(login()); return; }
    const content = h("main", {}, h("p", { class: "muted" }, "Loading…"));
    const nav = h("nav", {}, h("div", { class: "brand" }, "FPL Admin"),
      NAV.filter(([, , p]) => !p || can(p)).map(([id, label]) => h("button", { type: "button", "aria-current": id === page ? "page" : null, onclick: () => { page = id; flash = null; draw(); } }, label)),
      h("div", { class: "who" }, me.full_name, h("br"), me.role, h("br"), h("button", { class: "btn secondary gap-sm", onclick: async () => { try { await api("/auth/logout", { method: "POST" }); } catch (_) {} me = null; draw(); } }, "Sign out")));
    app.replaceChildren(h("div", { class: "layout" }, nav, content));
    try {
      const parts = await pages[page]();
      content.replaceChildren(...(flash ? [h("div", { class: "msg" + (flash.ok ? " ok" : ""), role: "status" }, flash.text)] : []), ...parts.filter(Boolean));
    } catch (e) { if (me) content.replaceChildren(h("div", { class: "msg", role: "alert" }, e.message)); }
  }

  (async () => { try { me = await api("/auth/me"); csrf = me.csrf_token; } catch (_) { me = null; } draw(); })();
})();
