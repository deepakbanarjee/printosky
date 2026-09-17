/**
 * The smallest set of view helpers the consoles actually need.
 *
 * No framework. The consoles are read-mostly lists that refresh on a timer, and
 * the cost of React here would be a build step on a repo that currently deploys
 * by pushing static files. What was missing was not a framework but *shared*
 * primitives: `money`, `badge` and `toast` are re-implemented in admin.html,
 * jobs.html and mis.html today, with three different rounding behaviours.
 */

/** Build an element. `el('div', {class:'card'}, [child, 'text'])`. */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/**
 * Paise → ₹1,23,456.50, with Indian digit grouping.
 * Mirrors core.money.format_rupees so a number reads the same in the console,
 * in a WhatsApp message and in the database.
 */
export function money(paise) {
  const value = (Number(paise) || 0) / 100;
  return "₹" + value.toLocaleString("en-IN", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

/** "4m ago" / "2h ago" — a queue is read in elapsed time, not wall clock. */
export function since(iso) {
  if (!iso) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (Number.isNaN(seconds)) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

const STATE_TONE = {
  held: "red", output_uncertain: "red", awaiting_approval: "amber",
  queued: "amber", claimed: "accent", spooled: "accent", printed: "accent",
  finishing: "accent", ready: "green", delivered: "muted", cancelled: "muted",
  unpaid: "red", part_paid: "amber", paid: "green", overpaid: "amber",
  refunded: "muted", written_off: "muted",
};

export function badge(text, tone) {
  const colour = tone || STATE_TONE[text] || "muted";
  return el("span", { class: `badge badge-${colour}` }, [String(text).replace(/_/g, " ")]);
}

let toastHost = null;
export function toast(message, kind = "ok") {
  if (!toastHost) {
    toastHost = el("div", { id: "toast-host" });
    document.body.append(toastHost);
  }
  const node = el("div", { class: `toast toast-${kind}`, role: "status" }, [message]);
  toastHost.append(node);
  setTimeout(() => node.remove(), kind === "error" ? 7000 : 3500);
}

/** An empty list must say why it is empty. An unexplained blank screen is
 *  indistinguishable from a broken one — the whole point of docs/FAIL_LOUD.md. */
export function emptyState(message, detail = "") {
  return el("div", { class: "empty" }, [
    el("div", { class: "empty-msg" }, [message]),
    detail ? el("div", { class: "empty-detail" }, [detail]) : null,
  ]);
}

export function errorState(err, onRetry) {
  return el("div", { class: "empty empty-error" }, [
    el("div", { class: "empty-msg" }, [err.message || "Something went wrong"]),
    el("div", { class: "empty-detail" }, [
      err.code ? `${err.code}${err.requestId ? ` · ${err.requestId}` : ""}` : "",
    ]),
    onRetry ? el("button", { class: "btn", onClick: onRetry }, ["Try again"]) : null,
  ]);
}
