/**
 * Operator console — "needs action first" (review §8.4).
 *
 * The ordering decision lives in the API (`GET /v2/queue` returns buckets in
 * priority order), not here. That is deliberate: what counts as urgent is a
 * business rule, it has to be the same on every screen, and a rule that lives
 * in one page's JavaScript is a rule the next page will get wrong.
 *
 * This file does three things: sign in, render the buckets, and act on a card.
 */

import { api, ApiError, token } from "./api.js";
import { badge, clear, el, emptyState, errorState, money, since, toast } from "./ui.js";

const REFRESH_MS = 20000;
const BUCKETS = [
  ["exceptions",      "Needs a decision", "Held, unconfirmed output, or waiting for approval."],
  ["paid_not_started", "Paid — not started", "Money is in. Nothing has been printed yet."],
  ["in_production",   "In production", "Claimed, spooling or printed."],
  ["finishing",       "Finishing", "Binding, lamination, transfers."],
  ["ready",           "Ready for pickup", "On the shelf, waiting for the customer."],
  ["unpaid",          "Unpaid", "Quoted, not yet collected."],
];

const state = { storeId: "", identity: null, timer: null };

// ── Boot ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("login-form").addEventListener("submit", onLogin);
  document.getElementById("logout").addEventListener("click", onLogout);
  document.getElementById("store-filter").addEventListener("change", (e) => {
    state.storeId = e.target.value;
    refresh();
  });
  if (token.get()) resume();
  else showLogin();
});

async function resume() {
  try {
    const data = await api.me();
    onSignedIn(data.identity);
  } catch {
    // An expired or revoked token is not an error worth showing — it is just
    // the sign-in screen.
    token.clear();
    showLogin();
  }
}

async function onLogin(event) {
  event.preventDefault();
  const input = document.getElementById("secret");
  const errorBox = document.getElementById("login-error");
  errorBox.textContent = "";
  try {
    const data = await api.login(input.value);
    token.set(data.token);
    input.value = "";
    onSignedIn(data.identity);
  } catch (err) {
    // Say what actually happened. "Incorrect PIN" when the server is missing
    // its signing key sends someone hunting for a typo for half an hour.
    errorBox.textContent =
      err.code === "not_configured" ? "Sign-in is not configured on the server yet."
      : err.code === "too_many_attempts" ? `Too many attempts — wait ${err.details.retry_after_seconds || 60}s.`
      : err.code === "network_error" ? "Cannot reach Printosky. Check the connection."
      : "That PIN did not match.";
  }
}

function onSignedIn(identity) {
  state.identity = identity;
  state.storeId = identity.store_ids.includes("*") ? "" : (identity.store_ids[0] || "");
  document.getElementById("login-screen").hidden = true;
  document.getElementById("console").hidden = false;
  document.getElementById("who").textContent = `${identity.display_name} · ${identity.role}`;

  const filter = document.getElementById("store-filter");
  clear(filter);
  const stores = identity.store_ids.includes("*") ? ["", "OSP", "PRINTK", "PRIOFF"] : identity.store_ids;
  for (const store of stores) {
    filter.append(el("option", { value: store, selected: store === state.storeId },
                     [store || "All stores"]));
  }
  refresh();
  state.timer = setInterval(refresh, REFRESH_MS);
}

async function onLogout() {
  clearInterval(state.timer);
  try { await api.logout(); } catch { /* the token is going away regardless */ }
  token.clear();
  location.reload();
}

function showLogin() {
  document.getElementById("login-screen").hidden = false;
  document.getElementById("console").hidden = true;
}

// ── Render ───────────────────────────────────────────────────────────────────

async function refresh() {
  const root = document.getElementById("queue");
  try {
    const [queue, health] = await Promise.all([api.queue(state.storeId), healthOrNull()]);
    renderHealth(health);
    renderQueue(root, queue);
    document.getElementById("updated").textContent = `updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) { showLogin(); return; }
    clear(root);
    root.append(errorState(err, refresh));
  }
}

async function healthOrNull() {
  try { return await api.health(); } catch { return null; }
}

function renderHealth(health) {
  const banner = document.getElementById("health");
  clear(banner);
  if (!health) {
    // Not knowing is itself worth saying. A banner that only appears when
    // things are broken is indistinguishable from a banner that is broken.
    banner.append(el("span", {}, ["Health unknown — the API did not answer."]));
    banner.hidden = false;
    return;
  }
  const failing = Object.entries(health.checks || {})
    .filter(([, check]) => check.ok === false)
    .map(([name, check]) => `${name}: ${check.detail}`);
  banner.hidden = failing.length === 0;
  if (failing.length) banner.append(el("span", {}, [failing.join(" · ")]));
}

function renderQueue(root, queue) {
  clear(root);
  const counts = queue.counts || {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) {
    root.append(emptyState("Nothing waiting.",
      state.storeId ? `No open orders at ${state.storeId}.` : "No open orders in any store."));
    return;
  }
  for (const [key, title, hint] of BUCKETS) {
    const cards = (queue.buckets || {})[key] || [];
    if (!cards.length) continue;
    root.append(el("section", { class: `bucket bucket-${key}` }, [
      el("header", { class: "bucket-head" }, [
        el("h2", {}, [title]),
        el("span", { class: "count" }, [String(cards.length)]),
        el("p", { class: "hint" }, [hint]),
      ]),
      el("div", { class: "cards" }, cards.map(orderCard)),
    ]));
  }
}

function orderCard(card) {
  const actions = [];
  if (card.production_state === "printed") {
    actions.push(action("Mark ready", () => transition(card.order_id, "ready")));
  }
  if (card.production_state === "ready") {
    actions.push(action("Handed over", () => transition(card.order_id, "delivered"),
                        card.balance_paise > 0 ? `${money(card.balance_paise)} still due` : ""));
  }
  if (card.production_state === "output_uncertain") {
    // The two ways out of an uncertain print, and nothing else. Automatically
    // reprinting is what F05/F06 exist to prevent.
    actions.push(action("Paper came out", () => transition(card.order_id, "printed")));
    actions.push(action("Nothing printed — requeue", () => transition(card.order_id, "queued")));
  }
  if (card.balance_paise > 0) {
    actions.push(action(`Take ${money(card.balance_paise)}`, () => takePayment(card)));
  }

  return el("article", { class: "card", dataset: { order: card.order_id } }, [
    el("div", { class: "card-top" }, [
      el("span", { class: "oid" }, [card.order_id]),
      badge(card.production_state),
      badge(card.payment_state),
      card.lane === "express" ? badge("express", "accent") : null,
    ]),
    el("div", { class: "card-who" }, [card.customer_name || "—"]),
    el("div", { class: "card-money" }, [
      money(card.total_paise),
      card.balance_paise > 0 ? el("span", { class: "due" }, [` · ${money(card.balance_paise)} due`]) : null,
      card.pickup_code ? el("span", { class: "pickup" }, [` · ${card.pickup_code}`]) : null,
    ]),
    el("div", { class: "card-time" }, [since(card.updated_at)]),
    actions.length ? el("div", { class: "card-actions" }, actions) : null,
  ]);
}

function action(label, handler, note = "") {
  return el("button", { class: "btn", title: note, onClick: async (e) => {
    e.target.disabled = true;
    try { await handler(); } finally { e.target.disabled = false; }
  } }, [label + (note ? ` (${note})` : "")]);
}

async function transition(orderId, to) {
  try {
    await api.transition(orderId, to, "operator console");
    toast(`${orderId} → ${to.replace(/_/g, " ")}`);
    refresh();
  } catch (err) {
    toast(err.message || "Could not update that order", "error");
  }
}

async function takePayment(card) {
  const typed = prompt(`Amount taken for ${card.order_id} (₹):`,
                       (card.balance_paise / 100).toFixed(2));
  if (typed === null) return;
  const rupees = Number(typed);
  if (!Number.isFinite(rupees) || rupees <= 0) { toast("That is not an amount", "error"); return; }
  const method = prompt("Method — cash or upi:", "cash");
  if (!method) return;
  const reference = prompt("Reference (receipt no., UPI ref) — needed so it cannot be counted twice:");
  if (!reference) { toast("A reference is required", "error"); return; }
  try {
    const result = await api.takePayment(card.order_id, {
      amount_paise: Math.round(rupees * 100),
      method: method.trim().toLowerCase(),
      reference: reference.trim(),
    });
    toast(result.duplicate ? "Already recorded — not counted twice" : "Payment recorded");
    refresh();
  } catch (err) {
    toast(err.message || "Could not record that payment", "error");
  }
}
