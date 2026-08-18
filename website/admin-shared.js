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
    // Scope by the fulfilling store (assigned_store_id), fall back to store_id.
    const owner = j.assigned_store_id || j.store_id;
    if (store !== "all" && owner !== store) return;
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
    + `&select=store_id,assigned_store_id,received_at,status,payment_mode,amount_collected&limit=5000`;
}

// ── Store / office identity (shared) ────────────────────────────────────────
// This machine's store code (e.g. OSP store PC, PRIOFF office). Read by
// admin.html / jobs.html (store-diag badge + default location filter) and
// mis.html (transcripts).
//
// Any PC whose print server is on a shop LAN is that shop's store PC, so it
// resolves to that store regardless of what /status last wrote to localStorage
// — this keeps each multi-PC shop's consoles all scoped to the one store.
// Off-LAN (localhost / store.printosky.com) falls back to the stored store_id,
// which /status supplies (and which the backend also forces on the LAN). Map
// mirrors store_config._DEFAULT_LAN_STORE_MAP.
//   192.168.55.* -> OSP (Thriprayar) · 192.168.1.* -> PRINTK (Nattika)
// Exempt ids (PRIOFF, the Nattika dev box that shares Printosky's 192.168.1.*
// subnet) keep their own id — /status reports it and the subnet rule is skipped.
const LAN_STORE_MAP = { "192.168.55.": "OSP", "192.168.1.": "PRINTK" };
const LAN_STORE_EXEMPT = ["PRIOFF"];

function getStoreId() {
  const stored = (localStorage.getItem("storeId") || "").trim();
  if (LAN_STORE_EXEMPT.includes(stored)) return stored;   // dev box stays itself
  try {
    const host = new URL(localStorage.getItem("storePcUrl") || "").hostname;
    for (const prefix in LAN_STORE_MAP) {
      if (host.startsWith(prefix)) return LAN_STORE_MAP[prefix];
    }
  } catch (e) { /* unset/invalid storePcUrl — fall through */ }
  return stored;
}

// ── Printer fleet per store (shared) ────────────────────────────────────────
// What is actually installed where, so a console never shows — or offers to
// print to — a machine the counter does not have:
//
//   OSP    (Thriprayar) : Konica Bizhub Pro 1100 (B&W) + Epson EM-C8100 (colour)
//   PRINTK (Nattika)    : Epson EM-C8100 only. A finishing/collection store with
//                         no Konica, so B&W prints on the Epson too — the store
//                         PC applies the same redirect server-side, see
//                         print_server._effective_printer_key().
//   PRIOFF (office)     : back-office box, no Konica.
//
// The Epson is an EM-C8100 everywhere: it replaced the WF-C21000 at OSP on
// 2026-06-29, and Nattika runs its own EM-C8100 (SPRINT_BACKLOG S11-4).
const EPSON_EM_C8100 = { key: "epson",  label: "Epson",  model: "EM-C8100" };
const KONICA_PRO_1100 = { key: "konica", label: "Konica", model: "Bizhub Pro 1100" };

const STORE_FLEETS = {
  OSP:    { konica: KONICA_PRO_1100, epson: EPSON_EM_C8100 },
  PRINTK: { konica: null,            epson: EPSON_EM_C8100 },
  PRIOFF: { konica: null,            epson: EPSON_EM_C8100 },
};

// Shop counters. PRIOFF is back-office, not a counter, so its consoles follow
// the location filter rather than being pinned to that box's own printers.
const COUNTER_STORE_IDS = ["OSP", "PRINTK"];

// Every printer any store has — the "All locations" union, and the safe default
// for a store code this page does not know about.
const FULL_FLEET = { konica: KONICA_PRO_1100, epson: EPSON_EM_C8100 };

function storeFleet(storeId) {
  if (!storeId || storeId === "all") return FULL_FLEET;
  return STORE_FLEETS[storeId] || FULL_FLEET;
}

// Which store's *hardware* the console is looking at. A shop counter only ever
// has its own printers, whichever location's jobs are on screen, so it stays
// pinned to its own store; the office/owner box follows the location filter.
function printerViewStore(locationFilter) {
  const machine = getStoreId();
  if (COUNTER_STORE_IDS.includes(machine)) return machine;
  return locationFilter || "all";
}

function storeHasKonica(storeId) {
  // For this machine's own store, its print server is the ground truth: any
  // store can drop its Konica in store_config.json, not just the ones mapped
  // above. /status reports has_konica; older store PCs omit it, so fall back.
  if (storeId && storeId !== "all" && storeId === getStoreId()) {
    const live = localStorage.getItem("machineHasKonica");
    if (live === "1") return true;
    if (live === "0") return false;
  }
  return !!storeFleet(storeId).konica;
}

// Printer keys to render, in display order, for a store.
function fleetPrinterKeys(storeId) {
  return storeHasKonica(storeId) ? ["konica", "epson"] : ["epson"];
}

// Full name for the UI, e.g. "Epson EM-C8100".
function printerLabel(key, storeId) {
  const p = storeFleet(storeId)[key];
  if (p) return `${p.label} ${p.model}`;
  return key === "epson" ? "Epson" : "Konica";
}

// Short name for tags and buttons, e.g. "Epson".
function printerShortLabel(key, storeId) {
  const p = storeFleet(storeId)[key];
  return p ? p.label : (key === "epson" ? "Epson" : "Konica");
}

// jobs.printer holds the Windows *queue* name the store PC dispatched to
// ("EM-C8100 Series(Network)", "KONICA MINOLTA 1100 PS") — a model, not a
// brand. Match on model families, or every EM-C8100 job reads as a Konica.
const EPSON_QUEUE_RE  = /epson|em-?c|wf-?c/i;
const KONICA_QUEUE_RE = /konica|minolta|bizhub/i;

function printerKeyFromName(name) {
  const s = String(name || "");
  if (EPSON_QUEUE_RE.test(s))  return "epson";
  if (KONICA_QUEUE_RE.test(s)) return "konica";
  return "";
}

// Mirror of print_server._effective_printer_key(): a B&W item at a store with
// no Konica actually prints on the Epson, so the console must say Epson.
function effectivePrinterKey(key, storeId) {
  return (key === "konica" && !storeHasKonica(storeId)) ? "epson" : key;
}

// ── Store identity in the header (shared) ───────────────────────────────────
// The console header is authored with the Oxygen name; a Nattika PC must not
// claim to be Oxygen. Unknown/blank store ids keep whatever the page ships with.
const STORE_NAMES = {
  OSP:    "Oxygen Students Paradise · Thriprayar",
  PRINTK: "Printosky · Nattika",
  PRIOFF: "Printosky Office · Nattika",
};

function storeName(storeId) {
  return STORE_NAMES[storeId] || "";
}

function renderStoreHeader() {
  const el = document.getElementById("hdr-store");
  const name = storeName(getStoreId());
  if (el && name) el.textContent = name;
}

// ── Fail-loud in the console (shared) ───────────────────────────────────────
// The rule: this page must never present a broken pipeline as an empty one.
//
// On 18 Aug 2026 the Nattika console showed a green "Live" dot, zero rows and
// the words "No Konica job records for this period" — while that store's
// printer data had not moved since the 11th. The dot only ever meant "Supabase
// answered". These helpers add the missing half: is the store PC reachable, are
// its checks passing, and is the data we are drawing actually current?
//
// Anything unhealthy gets a banner at the top of the page. Nothing unhealthy is
// ever left to be inferred from an empty table.

// Printer counters are written every 5 minutes by a healthy store PC, so
// silence this long means the pipeline is down, not quiet. Matches the cloud
// cron's STORE_COUNTER_STALE_MIN.
const HEALTH_STALE_MIN = 180;

async function fetchStorePcHealth() {
  const pcUrl = localStorage.getItem("storePcUrl");
  if (!pcUrl) return { reachable: null, reason: "store PC URL not set — use ⚙ PC Setup" };
  try {
    const r = await fetch(`${pcUrl}/health`);
    if (!r.ok) return { reachable: false, reason: `HTTP ${r.status} from ${pcUrl}` };
    return { reachable: true, data: await r.json() };
  } catch (e) {
    return { reachable: false, reason: `${pcUrl} did not answer (${e.message})` };
  }
}

// Minutes since this store last wrote a printer counter — null if it never has.
async function fetchCountersAgeMin(storeId) {
  const scope = storeId && storeId !== "all" ? `&store_id=eq.${storeId}` : "";
  try {
    const rows = await sbFetch("printer_counters",
      `select=polled_at&order=polled_at.desc&limit=1${scope}`);
    if (!rows || !rows.length) return null;
    const last = new Date(String(rows[0].polled_at).replace(" ", "T"));
    if (isNaN(last)) return null;
    return (Date.now() - last.getTime()) / 60000;
  } catch (e) {
    return undefined;            // could not tell — reported separately
  }
}

function humanAge(minutes) {
  if (minutes == null) return "never";
  if (minutes < 90) return `${Math.round(minutes)} min`;
  if (minutes < 2880) return `${(minutes / 60).toFixed(1)} h`;
  return `${Math.floor(minutes / 1440)} days`;
}

// Collect everything wrong right now. Returns [{level, title, detail}].
async function collectHealthProblems(storeId) {
  const problems = [];

  const pc = await fetchStorePcHealth();
  if (pc.reachable === false) {
    problems.push({
      level: "error",
      title: "Store PC unreachable",
      detail: `${pc.reason}. Printing and live status are unavailable; the rows below are whatever last reached the cloud.`,
    });
  } else if (pc.reachable && pc.data) {
    const wd = pc.data.watchdog || {};
    for (const name of (wd.failing || [])) {
      const c = (wd.checks || {})[name] || {};
      problems.push({
        level: "error",
        title: name,
        detail: `${c.detail || "check failing"}${c.for ? ` — for ${c.for}` : ""}`,
      });
    }
    if (pc.data.internet === false) {
      problems.push({ level: "warn", title: "Store PC is offline",
                      detail: "No internet at the store — payments and cloud sync are paused." });
    }
  }

  const age = await fetchCountersAgeMin(storeId);
  if (age === undefined) {
    problems.push({ level: "warn", title: "Could not check data freshness",
                    detail: "printer_counters query failed — treat the figures below as unverified." });
  } else if (age === null) {
    problems.push({ level: "warn", title: "No printer data for this location, ever",
                    detail: "This store has never written a printer counter. If it has printers, its poller has never worked." });
  } else if (age > HEALTH_STALE_MIN) {
    problems.push({
      level: "error",
      title: `Printer data is ${humanAge(age)} old`,
      detail: "The poller has stopped writing counters. Usually the printer is off or its IP changed — check the printer panel, then epson_ip/konica_ip in store_config.json on that PC.",
    });
  }
  return problems;
}

// Paint (or clear) the banner. Creates its own container under the header, so
// no page needs markup for it.
function renderHealthBanner(problems) {
  let host = document.getElementById("health-banner");
  if (!host) {
    host = document.createElement("div");
    host.id = "health-banner";
    const main = document.querySelector("main") || document.body;
    main.parentNode.insertBefore(host, main);
  }
  if (!problems || !problems.length) { host.innerHTML = ""; host.style.display = "none"; return; }

  const worst = problems.some(p => p.level === "error") ? "error" : "warn";
  const colour = worst === "error" ? "#ff5252" : "#ffab40";
  host.style.display = "block";
  host.style.cssText += `;display:block;margin:.6rem 1rem 0;border:1px solid ${colour};`
    + `border-left-width:4px;background:rgba(255,82,82,.08);padding:.6rem .9rem;`
    + `font-family:var(--mono,monospace);font-size:.72rem;line-height:1.5`;
  host.innerHTML =
    `<div style="color:${colour};font-weight:600;margin-bottom:.3rem">`
    + `⚠ ${problems.length} thing${problems.length > 1 ? "s are" : " is"} not working</div>`
    + problems.map(p =>
        `<div style="margin-top:.15rem"><span style="color:${p.level === "error" ? colour : "#ffab40"}">●</span> `
        + `<b>${escHealth(p.title)}</b> — ${escHealth(p.detail)}</div>`).join("");
}

function escHealth(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
}

// One call for a page to make on every load/refresh.
async function refreshHealthBanner(storeId) {
  try {
    renderHealthBanner(await collectHealthProblems(storeId));
  } catch (e) {
    // Even the health check failing is news — say so rather than showing nothing.
    renderHealthBanner([{ level: "warn", title: "Health check failed",
                          detail: e.message || String(e) }]);
  }
}
