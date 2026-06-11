// order-auth.js — detect a logged-in Printosky account on the order page so a
// registered customer skips re-entering their name + WhatsApp number.
//
// Mirrors account.html's auth model: a persisted Supabase session (Google /
// email magic-link) is the ONLY reload-surviving identity — the WhatsApp-OTP
// web_token lives in memory on account.html and is gone by the time someone
// lands here. So "logged in" on the order page == a Supabase session.
//
// That session's access_token (a Supabase JWT) is sent as `Authorization:
// Bearer` on /order/create; the backend (`_resolve_account`) maps it to the
// account's linked phone — the trusted identity. An account WITHOUT a linked
// phone (needs_phone_link) has no trusted number, so we must keep the WhatsApp
// field visible and let the backend fall back to the typed value.

const SB_URL  = 'https://mlhuwlnwwwxdnqafelko.supabase.co';
// Public anon (publishable) key — RLS-protected, already shipped client-side in
// account.html / notes.html. Not a secret.
const SB_ANON =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1saHV3bG53d3d4ZG5xYWZlbGtvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMyOTcwODksImV4cCI6MjA4ODg3MzA4OX0.2qrM3-BFDnSSICRMR8-pE6VdvVSJVhxG_cGZtBMDEnI';
const API     = 'https://printosky.vercel.app';

let _token = null;     // Bearer for /order/create (web_token or Supabase JWT), or null

// WhatsApp-OTP session persisted by account.html — a verified-phone identity
// that, unlike the Supabase session, survives reloads via our own storage key.
const WEB_TOKEN_KEY = 'psky_web_token';

// The Bearer token order-ui.js attaches to /order/create, or null when signed out.
export function authToken() { return _token; }

function clearToken() {
  _token = null;
  try { localStorage.removeItem(WEB_TOKEN_KEY); } catch { /* storage blocked */ }
}

const $ = (id) => document.getElementById(id);
const hide = (el) => el && el.classList.add('ov2-hidden');
const show = (el) => el && el.classList.remove('ov2-hidden');

// Detect a logged-in account and adapt the Step 2 identity fields. Best-effort:
// any failure (CDN blocked, network, expired token) silently leaves the guest
// form intact. Returns true when an account banner was shown.
export async function initAccount() {
  const sdk = window.supabase;
  const sb = (sdk && sdk.createClient) ? sdk.createClient(SB_URL, SB_ANON) : null;

  // Identity sources, strongest first:
  //  1. WhatsApp-OTP web_token (verified phone) persisted by account.html.
  //  2. Supabase session JWT (Google / email magic-link).
  let metaName = '';
  let token = null;
  try { token = localStorage.getItem(WEB_TOKEN_KEY) || null; } catch { /* storage blocked */ }

  if (!token && sb) {
    try {
      const { data: { session } } = await sb.auth.getSession();
      if (session && session.access_token) {
        token = session.access_token;
        const user = session.user || {};
        const meta = user.user_metadata || {};
        metaName = (meta.full_name || meta.name || '').trim()
                || (user.email ? user.email.split('@')[0] : '');
      }
    } catch { /* ignore — fall through to guest */ }
  }
  if (!token) return false;
  _token = token;

  // Resolve the trusted phone + display name from the account. A WhatsApp
  // web_token always carries a verified phone (needs_phone_link=false); a
  // Google/email login only when they've linked one.
  let name = metaName;
  let hasPhone = false;
  try {
    const r = await fetch(API + '/account/summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + _token },
      body: '{}',
    });
    if (r.status === 401) { clearToken(); return false; }   // expired → guest
    const d = await r.json().catch(() => ({}));
    if (r.ok) {
      hasPhone = !d.needs_phone_link;
      if (d.name && d.name.trim()) name = d.name.trim();    // stored account name wins
    }
  } catch {
    // Network hiccup — keep the token (backend can still resolve it) but leave
    // the WhatsApp field visible so the order can't get stuck without a phone.
  }

  renderIdentity(sb, name, hasPhone);
  return true;
}

function renderIdentity(sb, name, hasPhone) {
  const banner = $('ov2-identity');
  if (banner) {
    $('ov2-identity-name').textContent = name || 'your account';
    show(banner);
    const out = $('ov2-logout');
    if (out) {
      out.addEventListener('click', async () => {
        try { if (sb) await sb.auth.signOut(); } catch { /* ignore */ }
        clearToken();
        window.location.reload();
      });
    }
  }

  // Known name → prefill + hide the name field. (Keep it visible if the provider
  // gave us nothing usable, so the order still carries a name.)
  if (name) {
    const nameIn = $('ov2-name');
    if (nameIn) nameIn.value = name;
    hide($('ov2-field-name'));
  }

  // Hide the WhatsApp field only when a trusted phone is on file; otherwise the
  // backend needs the typed number.
  if (hasPhone) hide($('ov2-field-wa'));
}
