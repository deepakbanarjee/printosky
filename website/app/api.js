/**
 * v2 API client — one place that knows the envelope, the token and the retries.
 *
 * Every v2 response is `{ok, data}` or `{ok:false, error:{code,message,details}}`.
 * `call()` unwraps the success case and throws an `ApiError` carrying `code`
 * otherwise, so a page branches on `err.code === 'pricing_unavailable'` rather
 * than matching on prose. Compare with the legacy pages, which each re-derive
 * "was that an error?" from a mix of `res.ok`, `data.error` and `data.ok`.
 *
 * Retries: only idempotent verbs, only on 503/502/504 and network failures, and
 * only three times with backoff. A POST that creates an order is never retried
 * automatically — a duplicate order is worse than an error message.
 */

const DEFAULT_BASE = "https://printosky.vercel.app";
const TOKEN_KEY = "pk2_token";
const RETRY_STATUSES = new Set([502, 503, 504]);
const RETRY_METHODS = new Set(["GET", "HEAD"]);

export class ApiError extends Error {
  constructor(code, message, { status = 0, details = {}, requestId = "" } = {}) {
    super(message || code);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
    this.requestId = requestId;
  }

  /** True when trying the same thing again could plausibly work. */
  get retryable() {
    return this.status === 0 || RETRY_STATUSES.has(this.status) ||
           this.code === "dependency_unavailable";
  }
}

export const token = {
  get: () => { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } },
  set: (value) => { try { sessionStorage.setItem(TOKEN_KEY, value); } catch { /* private mode */ } },
  clear: () => { try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } },
};

export function apiBase() {
  // Same-origin on Vercel; explicit base when the page is served from Netlify.
  return window.PRINTOSKY_API_BASE || DEFAULT_BASE;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function call(method, path, { body, query, timeoutMs = 20000, retries } = {}) {
  const url = new URL(apiBase() + path);
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const attempts = retries ?? (RETRY_METHODS.has(method) ? 3 : 1);
  let lastError;

  for (let attempt = 0; attempt < attempts; attempt++) {
    if (attempt) await sleep(400 * 2 ** (attempt - 1));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const auth = token.get();
      const res = await fetch(url, {
        method,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const requestId = res.headers.get("X-Request-Id") || "";
      let payload = null;
      try { payload = await res.json(); } catch { /* non-JSON body handled below */ }

      if (payload && payload.ok) return payload.data;

      const err = payload && payload.error
        ? new ApiError(payload.error.code, payload.error.message,
                       { status: res.status, details: payload.error.details, requestId })
        : new ApiError("bad_response", `HTTP ${res.status}`, { status: res.status, requestId });

      // 401 means this session is over; stop retrying and let the page react.
      if (res.status === 401) { token.clear(); throw err; }
      if (!RETRY_STATUSES.has(res.status) || !RETRY_METHODS.has(method)) throw err;
      lastError = err;
    } catch (exc) {
      clearTimeout(timer);
      if (exc instanceof ApiError) {
        if (!exc.retryable || !RETRY_METHODS.has(method)) throw exc;
        lastError = exc;
        continue;
      }
      lastError = new ApiError("network_error", "could not reach Printosky", { status: 0 });
      if (!RETRY_METHODS.has(method)) throw lastError;
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}

export const api = {
  health: () => call("GET", "/v2/health", { query: { deep: 1 } }),
  login: (secret) => call("POST", "/v2/auth/login", { body: { secret } }),
  me: () => call("GET", "/v2/auth/me"),
  logout: () => call("POST", "/v2/auth/logout"),
  queue: (storeId) => call("GET", "/v2/queue", { query: { store_id: storeId } }),
  orders: (params) => call("GET", "/v2/orders", { query: params }),
  order: (id) => call("GET", `/v2/orders/${encodeURIComponent(id)}`),
  quote: (spec) => call("POST", "/v2/quotes", { body: spec }),
  transition: (id, to, reason) =>
    call("POST", `/v2/orders/${encodeURIComponent(id)}/transition`, { body: { to, reason } }),
  takePayment: (id, payload) =>
    call("POST", `/v2/orders/${encodeURIComponent(id)}/payments`, { body: payload }),
  payments: (id) => call("GET", `/v2/orders/${encodeURIComponent(id)}/payments`),
};
