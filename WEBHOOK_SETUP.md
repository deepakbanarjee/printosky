# Printosky Webhook Setup Guide
# Razorpay Payment Auto-Confirmation

## What this does
When a customer pays via the Razorpay link, Razorpay sends a webhook to your store PC.
The system automatically:
- Updates job status to Paid
- Sends customer "Payment confirmed, printing now" on WhatsApp
- Alerts staff on the watcher console

---

## Step 1: Install Cloudflare Tunnel (once only)

Cloudflare Tunnel makes your store PC reachable from the internet — free, no port forwarding.

1. Go to: https://github.com/cloudflare/cloudflared/releases/latest
2. Download: `cloudflared-windows-amd64.exe`
3. Rename it to: `cloudflared.exe`
4. Move it to: `C:\printosky_watcher\cloudflared.exe`

---

## Step 2: Start the tunnel

Double-click `START_TUNNEL.bat` (created below) OR run in terminal:

```
C:\printosky_watcher\cloudflared.exe tunnel --url http://localhost:3002
```

You'll see output like:
```
INF | Your quick Tunnel has been created! Visit it at:
INF | https://random-name-here.trycloudflare.com
```

**Copy that URL** — e.g. `https://random-name-here.trycloudflare.com`

Your webhook URL is:
`https://random-name-here.trycloudflare.com/webhook/razorpay`

---

## Step 3: Set webhook in Razorpay Dashboard

1. Go to: https://dashboard.razorpay.com
2. Settings → Webhooks → Add New Webhook
3. Webhook URL: `https://random-name-here.trycloudflare.com/webhook/razorpay`
4. Secret: `PrintoskyWebhook2026`
5. Active Events — tick these:
   - ✅ payment_link.paid
   - ✅ payment.captured
6. Save

---

## Step 4: Update WEBHOOK_SECRET

Open `C:\printosky_watcher\razorpay_integration.py`
Find: `WEBHOOK_SECRET = "PrintoskyWebhook2026"`
Change to whatever secret you set in Step 3 (must match exactly).

---

## Important: Tunnel URL changes on restart

The free Cloudflare Tunnel gives a new URL every time you restart it.
Each time:
1. Get new URL from tunnel output
2. Update webhook URL in Razorpay Dashboard → Webhooks → Edit

**To avoid this:** Create a free Cloudflare account and set up a named tunnel
with a fixed subdomain (e.g. webhook.printosky.com). Ask me to set this up
when you're ready to go to production.

---

## START_TUNNEL.bat

Create this file at `C:\printosky_watcher\START_TUNNEL.bat`:

```bat
@echo off
title Printosky Cloudflare Tunnel
color 0B
echo.
echo  ==========================================
echo   PRINTOSKY CLOUDFLARE TUNNEL
echo   Exposing webhook receiver to internet
echo  ==========================================
echo.
echo  Starting tunnel on port 3002...
echo  Copy the URL shown below and set it in:
echo  Razorpay Dashboard → Settings → Webhooks
echo.
C:\printosky_watcher\cloudflared.exe tunnel --url http://localhost:3002
pause
```

---

## Verify it's working

1. Start watcher (`START_PRINTOSKY.bat`)
2. Start tunnel (`START_TUNNEL.bat`)
3. Open: `https://your-tunnel-url.trycloudflare.com`
   — should show: "Printosky webhook receiver OK"
4. In Razorpay Dashboard → Webhooks → click "Test" → should show 200 OK

---

## Going live (when ready)

1. Replace test keys in `razorpay_integration.py`:
   - `RAZORPAY_KEY_ID = "rzp_live_..."`
   - `RAZORPAY_KEY_SECRET = "your_live_secret"`
2. Update same keys in `admin.html`
3. Set up fixed Cloudflare tunnel (no URL change on restart)
