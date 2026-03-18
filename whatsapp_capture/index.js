/**
 * PRINTOSKY WHATSAPP AUTO-CAPTURE
 * ================================
 * Monitors WhatsApp (8943232033 - Oxygen) for incoming files.
 * Auto-saves every attachment to C:\Printosky\Jobs\Incoming\
 * Hot folder watcher picks it up instantly → logged to DB.
 * Also sends auto-reply to customer confirming receipt.
 *
 * Run: node index.js
 * First run: scan QR code with WhatsApp on the store phone.
 * After that: runs silently, no QR needed again.
 */

const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");
const qrcode  = require("qrcode-terminal");
const fs      = require("fs");
const path    = require("path");

// ── Config ────────────────────────────────────────────────────────────────────
const HOT_FOLDER   = "C:\\Printosky\\Jobs\\Incoming";
const SESSION_DIR  = path.join(__dirname, ".wwebjs_auth");
const LOG_FILE     = path.join(__dirname, "whatsapp.log");

// Auto-reply message sent to customer when file is received
const AUTO_REPLY = `✅ *File received!*

Your print job has been registered at Printosky / Oxygen Students Paradise.

📋 Job ID will be confirmed shortly
⏱ Ready time will be shared once we review your file
📞 Questions? Reply here or call: 8943232033

_Thank you for choosing Printosky!_ 🖨️`;

// File types we capture (everything printable)
const CAPTURE_MIME = [
  "application/pdf",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-powerpoint",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/gif",
  "image/webp",
  "image/tiff",
];

// ── Ensure hot folder exists ──────────────────────────────────────────────────
if (!fs.existsSync(HOT_FOLDER)) {
  fs.mkdirSync(HOT_FOLDER, { recursive: true });
  log(`Created hot folder: ${HOT_FOLDER}`);
}

// ── Logging ───────────────────────────────────────────────────────────────────
function log(msg) {
  const ts = new Date().toISOString().replace("T", " ").slice(0, 19);
  const line = `[${ts}] ${msg}`;
  console.log(line);
  fs.appendFileSync(LOG_FILE, line + "\n");
}

// ── Sanitize filename ─────────────────────────────────────────────────────────
function safeFilename(sender, originalName, ext) {
  const ts       = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const phone    = sender.replace(/[^0-9]/g, "").slice(-10);
  const baseName = originalName
    ? originalName.replace(/[^a-zA-Z0-9._\- ]/g, "_").slice(0, 60)
    : `WhatsApp_${phone}`;
  return `WA_${ts}_${baseName}${ext && !baseName.endsWith(ext) ? ext : ""}`;
}

// ── Extension from MIME ───────────────────────────────────────────────────────
function extFromMime(mime) {
  const map = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
  };
  return map[mime] || "";
}

// ── WhatsApp Client ───────────────────────────────────────────────────────────
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: SESSION_DIR }),
  puppeteer: {
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

// QR code — scan once with store phone
client.on("qr", (qr) => {
  console.log("\n========================================");
  console.log("  SCAN THIS QR CODE WITH WHATSAPP");
  console.log("  (Store phone: 8943232033)");
  console.log("  Open WhatsApp → Linked Devices → Link a Device");
  console.log("========================================\n");
  qrcode.generate(qr, { small: true });
});

client.on("authenticated", () => {
  log("WhatsApp authenticated — session saved, no QR needed next time");
});

client.on("auth_failure", (msg) => {
  log(`AUTH FAILED: ${msg} — delete .wwebjs_auth folder and restart`);
});

client.on("ready", () => {
  log("✅ WhatsApp capture READY — monitoring 8943232033 for incoming files");
  console.log("\n  All incoming files will be auto-saved to:");
  console.log(`  ${HOT_FOLDER}\n`);
});

client.on("disconnected", (reason) => {
  log(`WhatsApp disconnected: ${reason} — restarting in 10s`);
  setTimeout(() => client.initialize(), 10000);
});

// ── Bot relay: forward replies to Python bot via HTTP ────────────────────────
async function sendBotReply(phone, text) {
  try {
    const res = await fetch("http://localhost:3003/bot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone, text }),
    });
    const data = await res.json();
    if (data.replies && data.replies.length > 0) {
      for (const reply of data.replies) {
        const chatId = phone.includes("@c.us") ? phone : `${phone}@c.us`;
        await client.sendMessage(chatId, reply);
        await new Promise(r => setTimeout(r, 500)); // small delay between messages
      }
    }
  } catch (e) {
    log(`Bot relay error: ${e.message}`);
  }
}

// ── Main: handle incoming messages ───────────────────────────────────────────
client.on("message", async (msg) => {
  try {
    if (msg.fromMe) return;

    // Ignore WhatsApp status updates (from status@broadcast)
    if (msg.from === "status@broadcast") return;
    if (msg.isStatus) return;

    // ── Only process individual (1-to-1) chats ────────────────────────────
    // Groups end with @g.us, newsletters/channels end with @newsletter
    // Communities are group-based so also caught by @g.us check
    if (msg.from.endsWith("@g.us"))         { log(`⏭ Skipping group message from ${msg.from}`);       return; }
    if (msg.from.endsWith("@newsletter"))   { log(`⏭ Skipping newsletter/channel from ${msg.from}`);  return; }
    if (msg.from.endsWith("@broadcast"))    { log(`⏭ Skipping broadcast from ${msg.from}`);           return; }
    if (msg.isGroupMsg)                      { log(`⏭ Skipping group message (isGroupMsg) ${msg.from}`); return; }
    // Only proceed if it's a genuine individual contact
    if (!msg.from.endsWith("@c.us") && !msg.from.endsWith("@lid")) {
      log(`⏭ Skipping unknown chat type: ${msg.from}`);
      return;
    }

    const sender = msg.from;

    // Resolve real phone number — WhatsApp may give LID instead of actual number
    // msg.getContact() resolves LID → real contact with actual phone number
    let phone;
    try {
      const contact = await msg.getContact();
      // contact.number is the real phone number (e.g. 919495706405)
      phone = (contact.number || "").replace(/[^0-9]/g, "");
      if (!phone) throw new Error("empty contact.number");
    } catch (e) {
      // Fallback: strip @c.us/@lid and use raw digits
      phone = sender.replace("@c.us", "").replace("@lid", "").replace(/[^0-9]/g, "");
    }
    // Ensure Indian numbers have country code
    if (phone.length === 10 && /^[6-9]/.test(phone)) {
      phone = "91" + phone;
    }
    log(`  📞 Resolved sender: ${sender} → ${phone}`);

    // ── Text message: route to bot conversation ───────────────────────────
    if (!msg.hasMedia) {
      log(`💬 Text from ${phone}: "${msg.body.slice(0, 50)}"`);
      await sendBotReply(phone, msg.body);
      return;
    }

    // ── Media message: capture file ───────────────────────────────────────
    log(`📥 File incoming from ${phone} — downloading…`);
    const media = await msg.downloadMedia();
    if (!media || !media.data) {
      log(`  ⚠️ Could not download media from ${phone}`);
      return;
    }

    const mime = media.mimetype ? media.mimetype.split(";")[0].trim() : "";
    if (!CAPTURE_MIME.includes(mime)) {
      log(`  ⏭ Skipping non-printable file type: ${mime}`);
      return;
    }

    const ext      = extFromMime(mime);
    const filename = safeFilename(phone, media.filename, ext);
    const destPath = path.join(HOT_FOLDER, filename);
    const buffer   = Buffer.from(media.data, "base64");
    fs.writeFileSync(destPath, buffer);

    const sizeKb = (buffer.length / 1024).toFixed(1);
    log(`  ✅ Saved: ${filename} (${sizeKb} KB) from ${phone}`);

    // Write sidecar file so watcher knows the sender's phone number
    const senderFile = destPath + ".sender";
    fs.writeFileSync(senderFile, phone);
    log(`  📌 Sender tag written: ${phone}`);

  } catch (err) {
    log(`  ❌ Error processing message: ${err.message}`);
  }
});

// ── Start ─────────────────────────────────────────────────────────────────────
log("Starting Printosky WhatsApp capture…");
client.initialize();

// ── HTTP Send Server (localhost:3001) ─────────────────────────────────────────
// watcher.py posts here to send WhatsApp messages (job tokens, ready alerts)
const http = require("http");

const sendServer = http.createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/send") {
    res.writeHead(404); res.end(); return;
  }
  let body = "";
  req.on("data", chunk => body += chunk);
  req.on("end", async () => {
    try {
      const { phone, message } = JSON.parse(body);
      if (!phone || !message) {
        res.writeHead(400); res.end(JSON.stringify({ error: "phone and message required" })); return;
      }
      // Resolve number to WhatsApp ID (handles cases where chat doesn't exist yet)
      let chatId;
      try {
        const numberId = await client.getNumberId(phone.replace("@c.us", ""));
        chatId = numberId ? numberId._serialized : (phone.includes("@c.us") ? phone : `${phone}@c.us`);
      } catch (e) {
        chatId = phone.includes("@c.us") ? phone : `${phone}@c.us`;
      }
      await client.sendMessage(chatId, message);
      log(`📤 Sent message to ${phone} (${chatId})`);
      res.writeHead(200); res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      log(`❌ Send error: ${err.message}`);
      res.writeHead(500); res.end(JSON.stringify({ error: err.message }));
    }
  });
});

// ── Send document endpoint (for PDF invoices) ────────────────────────────────
// Accepts: multipart/form-data with fields: phone, caption, file (PDF)
// Returns: { ok: true } or { error: "..." }
const http2 = require("http");
const sendDocServer = http2.createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/send-document") {
    res.writeHead(404); res.end(); return;
  }
  const chunks = [];
  req.on("data", c => chunks.push(c));
  req.on("end", async () => {
    try {
      const buf = Buffer.concat(chunks);
      const ct  = req.headers["content-type"] || "";
      const bnd = ct.includes("boundary=") ? ct.split("boundary=")[1].trim() : null;
      if (!bnd) { res.writeHead(400); res.end(JSON.stringify({error:"no boundary"})); return; }

      // Parse multipart manually
      const sep   = Buffer.from("--" + bnd);
      const parts = [];
      let   pos   = 0;
      while (pos < buf.length) {
        const idx = buf.indexOf(sep, pos);
        if (idx === -1) break;
        const next = buf.indexOf(sep, idx + sep.length);
        if (next === -1) break;
        const chunk = buf.slice(idx + sep.length + 2, next - 2); // skip \r\n
        const headerEnd = chunk.indexOf(Buffer.from("\r\n\r\n"));
        if (headerEnd !== -1) {
          const headers = chunk.slice(0, headerEnd).toString("utf8");
          const body    = chunk.slice(headerEnd + 4);
          parts.push({ headers, body });
        }
        pos = next;
      }

      let phone = null, caption = "", fname = "invoice.pdf", fileData = null;
      for (const part of parts) {
        const nameMatch = part.headers.match(/name="([^"]+)"/);
        const fileMatch = part.headers.match(/filename="([^"]+)"/);
        if (!nameMatch) continue;
        const fieldName = nameMatch[1];
        if (fileMatch) {
          fname    = fileMatch[1];
          fileData = part.body;
        } else if (fieldName === "phone") {
          phone = part.body.toString("utf8").trim();
        } else if (fieldName === "caption") {
          caption = part.body.toString("utf8").trim();
        }
      }

      if (!phone)    { res.writeHead(400); res.end(JSON.stringify({error:"phone required"})); return; }
      if (!fileData) { res.writeHead(400); res.end(JSON.stringify({error:"no file data"}));   return; }

      const { MessageMedia } = require("whatsapp-web.js");
      const media  = new MessageMedia("application/pdf", fileData.toString("base64"), fname);
      const chatId = phone.includes("@c.us") ? phone : (phone + "@c.us");
      await client.sendMessage(chatId, media, { caption });
      log("📄 Invoice sent to " + phone + ": " + fname);
      res.writeHead(200); res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      log("❌ send-document error: " + err.message);
      res.writeHead(500); res.end(JSON.stringify({ error: err.message }));
    }
  });
});
sendDocServer.listen(3004, "127.0.0.1", () => {
  log("Document send server listening on localhost:3004");
});

sendServer.listen(3001, "127.0.0.1", () => {
  log("HTTP send server listening on localhost:3001");
});
