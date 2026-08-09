/*
 * admin-shared.js — helpers shared by admin.html and mis.html.
 *
 * Loaded as a classic (non-module) <script> BEFORE each page's own inline
 * <script>. Functions here are plain globals; they reference SUPABASE_URL /
 * SUPABASE_KEY, which each page declares in its own inline script (classic
 * scripts share one global lexical scope, and these are only read at call
 * time, so declaration order does not matter).
 *
 * Auth-fail behaviour differs per page (admin logs the staff session out of
 * the store PC; mis clears its session and reloads), so sbFetch delegates the
 * 401 case to a page-provided sbAuthFail() hook instead of hard-calling one
 * page's logout(). Pages define sbAuthFail(); if a page omits it, sbFetch
 * falls back to a safe clear-and-reload.
 */

// ── Supabase REST fetch (shared) ─────────────────────────────────────────────
async function sbFetch(table, params = "") {
  const jwt = sessionStorage.getItem("supabase_jwt") || SUPABASE_KEY;
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${params}`, {
    headers: {
      "apikey": SUPABASE_KEY,
      "Authorization": `Bearer ${jwt}`,
    }
  });
  if (r.status === 401) {
    if (typeof sbAuthFail === "function") sbAuthFail();
    else { sessionStorage.clear(); location.reload(); }
    return null;
  }
  if (!r.ok) throw new Error(`${table}: ${r.status}`);
  return r.json();
}

// ── Lightweight non-blocking toast (shared) ─────────────────────────────────
// Types: "ok" (green), "error" (red), else neutral. There is no toast markup
// in either page, so the container is created lazily on first use.
function showToast(msg, type = "ok") {
  let host = document.getElementById("toast-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "toast-host";
    host.style.cssText = "position:fixed;bottom:20px;left:50%;transform:translateX(-50%);"
      + "z-index:10000;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none";
    document.body.appendChild(host);
  }
  const colour = type === "error" ? "var(--red, #ff5252)"
               : type === "ok"    ? "var(--green, #00e676)"
               : "var(--muted, #888)";
  const t = document.createElement("div");
  t.textContent = msg;
  t.style.cssText = "background:#1a1a1a;color:" + colour + ";border:1px solid " + colour + ";"
    + "border-radius:8px;padding:8px 14px;font-family:var(--mono,monospace);font-size:.8rem;"
    + "box-shadow:0 4px 16px rgba(0,0,0,.4);opacity:0;transition:opacity .18s;max-width:80vw";
  host.appendChild(t);
  requestAnimationFrame(() => { t.style.opacity = "1"; });
  setTimeout(() => {
    t.style.opacity = "0";
    setTimeout(() => t.remove(), 220);
  }, 2600);
}

// ── Daily stat aggregation (shared) ─────────────────────────────────────────
// The stat cards (jobs / done / pending / revenue / cash·upi) used to read the
// `daily_summary` table, which the store PC writes from its LOCAL SQLite — so
// it missed every job created directly in the cloud (the order API) and split
// cash/upi with a case-sensitive payment_mode match, showing ₹0. Aggregate the
// `jobs` table (the source of truth) instead. Mirrors the backend
// store_digest.summarize_jobs so the console and the owner's digest agree.
const SB_PENDING_STATUSES = new Set(
  ["received", "in progress", "pending", "paid", "queued", "ready", "printed"]);

function summarizeJobs(jobs, store = "all") {
  const agg = { total_jobs: 0, completed: 0, pending: 0, revenue: 0, cash: 0, upi: 0 };
  (jobs || []).forEach(j => {
    if (store !== "all" && j.store_id !== store) return;
    agg.total_jobs++;
    const st = String(j.status || "").trim().toLowerCase();
    if (st === "completed") agg.completed++;
    else if (SB_PENDING_STATUSES.has(st)) agg.pending++;
    const amt = Number(j.amount_collected) || 0;
    agg.revenue += amt;
    const pm = String(j.payment_mode || "").trim().toLowerCase();
    if (pm === "cash") agg.cash += amt;
    else if (pm === "upi") agg.upi += amt;
  });
  return agg;
}

// PostgREST params to fetch today's jobs (all stores) for stat aggregation.
// Date-only bounds compare lexicographically against the 'YYYY-MM-DD HH:MM:SS'
// received_at text and correctly cover both space- and 'T'-separated stamps.
// tomorrow is derived in UTC from the date string so it never collapses to
// today. Caller aggregates per-store client-side via summarizeJobs.
function todayJobsParams(todayStr) {
  const tomorrow = new Date(new Date(todayStr + "T00:00:00Z").getTime() + 864e5)
    .toISOString().slice(0, 10);
  return `received_at=gte.${todayStr}&received_at=lt.${tomorrow}`
    + `&select=store_id,received_at,status,payment_mode,amount_collected&limit=5000`;
}

// ── Store / office identity (shared) ────────────────────────────────────────
// This machine's store code (e.g. OSP store PC, PRIOFF office). Read by
// admin.html / jobs.html (store-diag badge + default location filter) and
// mis.html (transcripts).
//
// Any PC whose print server is on the Oxygen shop LAN (192.168.55.*) is an
// Oxygen store PC, so it resolves to OSP regardless of what /status last wrote
// to localStorage — this keeps the multi-PC shop's consoles all scoped to the
// one store. Off-LAN (localhost / store.printosky.com) falls back to the stored
// store_id, which /status supplies (and which the backend also forces to OSP on
// the LAN). Prefix mirrors store_config.OXYGEN_LAN_PREFIX.
const OXYGEN_LAN_PREFIX = "192.168.55.";

function getStoreId() {
  try {
    const host = new URL(localStorage.getItem("storePcUrl") || "").hostname;
    if (host.startsWith(OXYGEN_LAN_PREFIX)) return "OSP";
  } catch (e) { /* unset/invalid storePcUrl — fall through */ }
  return (localStorage.getItem("storeId") || "").trim();
}
