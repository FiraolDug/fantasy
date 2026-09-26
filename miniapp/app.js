// Point this at your deployed backend before publishing the Mini App.
const API_BASE = "http://localhost:8000";

const tg = window.Telegram ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); }

let authToken = null;
let currentGameweek = null;

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  setTimeout(() => (el.hidden = true), 3000);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${resp.status})`);
  }
  return resp.status === 204 ? null : resp.json();
}

async function authenticate() {
  if (!tg || !tg.initData) {
    toast("Open this from the Telegram bot to sign in.");
    return false;
  }
  const data = await api("/auth/telegram-webapp", {
    method: "POST",
    body: JSON.stringify({ init_data: tg.initData }),
  });
  authToken = data.access_token;
  return true;
}

// --- Tab navigation ---
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => showScreen(btn.dataset.target));
});

function showScreen(id) {
  document.querySelectorAll("[data-screen]").forEach((s) => (s.hidden = s.id !== id));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.target === id));
  if (id === "screen-games") loadGames();
  if (id === "screen-leaderboard") loadLeaderboard();
  if (id === "screen-wallet") loadWallet();
  if (id === "screen-profile") loadProfile();
}

// --- Home ---
async function loadHome() {
  try {
    const gw = await api("/gameweeks/current");
    currentGameweek = gw;
    document.getElementById("home-gw-title").textContent = `Gameweek ${gw.gw_number}`;
    document.getElementById("home-gw-deadline").textContent =
      `Deadline: ${new Date(gw.registration_deadline).toLocaleString()}`;
    document.getElementById("home-gw-status").textContent =
      gw.my_entry_status ? `Your entry: ${gw.my_entry_status}` : "You haven't joined yet.";
    document.getElementById("home-pool").textContent = `${gw.prize_pool} ETB`;
    document.getElementById("home-entrants").textContent = gw.participants;

    const joinBtn = document.getElementById("join-btn");
    if (gw.my_entry_status === "confirmed") {
      joinBtn.textContent = "Already joined";
      joinBtn.disabled = true;
    } else {
      joinBtn.textContent = `Join · ${gw.entry_fee} ETB`;
      joinBtn.disabled = false;
    }
  } catch (e) {
    document.getElementById("home-gw-title").textContent = "No open gameweek";
    document.getElementById("home-gw-deadline").textContent = e.message;
  }

  try {
    const wallet = await api("/wallet/me");
    document.getElementById("home-balance").textContent = `${wallet.available_balance} ETB`;
  } catch (e) { /* ignore, wallet screen will show the error */ }
}

document.getElementById("join-btn").addEventListener("click", async () => {
  if (!currentGameweek) return;
  try {
    await api(`/gameweeks/${currentGameweek.id}/join`, { method: "POST" });
    toast("You're in! Good luck this gameweek.");
    loadHome();
  } catch (e) {
    toast(e.message);
  }
});

// --- Games ---
async function loadGames() {
  const el = document.getElementById("games-card");
  try {
    const gw = await api("/gameweeks/current");
    el.innerHTML = `
      <div class="list-row"><span>Gameweek</span><span>${gw.gw_number}</span></div>
      <div class="list-row"><span>Entry fee</span><span>${gw.entry_fee} ETB</span></div>
      <div class="list-row"><span>Entrants</span><span>${gw.participants}</span></div>
      <div class="list-row"><span>Prize pool</span><span>${gw.prize_pool} ETB</span></div>
      <div class="list-row"><span>Deadline</span><span>${new Date(gw.registration_deadline).toLocaleString()}</span></div>
      <div class="list-row"><span>Status</span><span>${gw.status}</span></div>
    `;
  } catch (e) {
    el.textContent = e.message;
  }
}

// --- Leaderboard ---
async function loadLeaderboard() {
  const el = document.getElementById("leaderboard-list");
  el.innerHTML = "Loading…";
  try {
    const gw = currentGameweek || (await api("/gameweeks/current"));
    const rows = await api(`/gameweeks/${gw.id}/leaderboard`);
    if (rows.length === 0) {
      el.innerHTML = '<p class="muted">No scores yet — check back once the gameweek is live.</p>';
      return;
    }
    el.innerHTML = rows
      .map(
        (r, i) => `
        <div class="list-row ${r.is_me ? "me" : ""}">
          <span class="rank">${r.rank ?? i + 1}</span>
          <span class="name">${r.display_name}</span>
          <span class="points">${r.points} pts</span>
        </div>`
      )
      .join("");
  } catch (e) {
    el.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

// --- Wallet ---
async function loadWallet() {
  try {
    const wallet = await api("/wallet/me");
    document.getElementById("wallet-balance").textContent = `${wallet.available_balance} ETB`;
  } catch (e) { toast(e.message); }

  try {
    const instr = await api("/deposits/instructions");
    document.getElementById("deposit-instructions").innerHTML =
      `Telebirr: <b>${instr.telebirr_receiver_number}</b> (${instr.telebirr_receiver_name})<br>` +
      `CBE: <b>${instr.cbe_account_number}</b> (${instr.cbe_receiver_name})<br>${instr.instructions}`;
  } catch (e) { /* ignore */ }

  try {
    const deposits = await api("/deposits/mine");
    const el = document.getElementById("deposit-history");
    el.innerHTML = deposits.length
      ? deposits
          .map(
            (d) => `
        <div class="list-row">
          <span>${d.method} · ${d.transaction_id}</span>
          <span>${d.amount} ETB — ${d.status}</span>
        </div>`
          )
          .join("")
      : '<p class="muted">No deposits yet.</p>';
  } catch (e) { /* ignore */ }
}

document.getElementById("deposit-submit").addEventListener("click", async () => {
  const method = document.getElementById("deposit-method").value;
  const amount = parseFloat(document.getElementById("deposit-amount").value);
  const transaction_id = document.getElementById("deposit-txn").value.trim();
  if (!amount || !transaction_id) {
    toast("Enter an amount and transaction ID.");
    return;
  }
  try {
    await api("/deposits", {
      method: "POST",
      body: JSON.stringify({ method, amount, transaction_id }),
    });
    toast("Submitted — you'll be credited once an admin verifies it.");
    document.getElementById("deposit-amount").value = "";
    document.getElementById("deposit-txn").value = "";
    loadWallet();
  } catch (e) {
    toast(e.message);
  }
});

// --- Profile ---
async function loadProfile() {
  try {
    const p = await api("/users/me/profile");
    document.getElementById("profile-name").textContent = p.full_name || "My Profile";
    document.getElementById("profile-team").textContent = p.fpl_team_name || "Not set";
    document.getElementById("profile-manager-id").textContent = p.fpl_manager_id || "—";
    document.getElementById("profile-gws").textContent = p.total_gameweeks;
    document.getElementById("profile-best-rank").textContent = p.best_rank ?? "—";
    document.getElementById("profile-winnings").textContent = `${p.total_winnings} ETB`;
  } catch (e) {
    toast(e.message);
  }
}

// --- Boot ---
(async function init() {
  const ok = await authenticate().catch((e) => {
    toast(e.message);
    return false;
  });
  if (ok) loadHome();
})();
