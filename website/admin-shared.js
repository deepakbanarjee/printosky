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
