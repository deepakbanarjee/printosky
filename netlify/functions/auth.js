/**
 * Printosky Auth Function
 * Verifies admin/superadmin/store/mis passwords server-side.
 * Hashes are stored in Netlify environment variables — never in source code.
 *
 * POST /.netlify/functions/auth
 * Body: { type: "admin" | "superadmin" | "store" | "mis", password: string }
 * Returns: { ok: boolean }
 */

const crypto = require("crypto");

const ENV_KEY = {
  superadmin: "SUPERADMIN_SHA256_HASH",
  store:      "STORE_SHA256_HASH",
  mis:        "MIS_SHA256_HASH",
  staff:      "STAFF_TOKEN_HASH",
};

exports.handler = async (event) => {
  if (event.httpMethod !== "POST") {
    return { statusCode: 405, body: "Method Not Allowed" };
  }

  let type, password;
  try {
    ({ type, password } = JSON.parse(event.body));
  } catch {
    return { statusCode: 400, body: "Bad Request" };
  }

  if (!type || !password) {
    return { statusCode: 400, body: "Bad Request" };
  }

  let ok = false;

  if (type === "admin") {
    const expectedHash = process.env.ADMIN_PBKDF2_HASH;
    const saltHex      = process.env.ADMIN_PBKDF2_SALT;
    if (expectedHash && saltHex) {
      const salt = Buffer.from(saltHex, "hex");
      const hash = await new Promise((resolve, reject) => {
        crypto.pbkdf2(password, salt, 600000, 32, "sha256", (err, key) => {
          if (err) reject(err); else resolve(key.toString("hex"));
        });
      });
      ok = hash === expectedHash;
    }
  } else if (ENV_KEY[type]) {
    const expectedHash = process.env[ENV_KEY[type]];
    if (expectedHash) {
      const hash = crypto.createHash("sha256").update(password).digest("hex");
      ok = hash === expectedHash;
    }
  }

  if (!ok) {
    return {
      statusCode: 200,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ok: false }),
    };
  }

  // Custom password verified — now sign into Supabase Auth to get a JWT
  // for use in RLS-protected queries.
  let supabase_jwt = null;
  try {
    const supabaseUrl = process.env.SUPABASE_URL;
    const supabaseKey = process.env.SUPABASE_KEY;
    const authEmail   = process.env.SUPABASE_AUTH_EMAIL;
    const authPassword = process.env.SUPABASE_AUTH_PASSWORD;
    const r = await fetch(`${supabaseUrl}/auth/v1/token?grant_type=password`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "apikey": supabaseKey },
      body: JSON.stringify({ email: authEmail, password: authPassword }),
    });
    const data = await r.json();
    supabase_jwt = data.access_token || null;
  } catch { /* supabase_jwt stays null — frontend will catch */ }

  return {
    statusCode: 200,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ok: true, supabase_jwt }),
  };
};
