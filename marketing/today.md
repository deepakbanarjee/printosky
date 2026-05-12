# Printosky — Today's Sprint (Mon 2026-05-04)

> **One goal:** by tonight, the funnel has moved from 0 to ≥1 real referral. That's it.
>
> **Single metric:** `SELECT COUNT(*) FROM bot_sessions WHERE referral_code IS NOT NULL`. Now: 0.

---

## Order of operations (estimated 2 h total)

| # | Task | Time | Done |
|---|---|---|---|
| 1 | **Send broadcast to top 5** customers — fastest signal, zero leg-work | 15 min | ☐ |
| 2 | **Print + tape the poster** at counter and front door | 20 min | ☐ |
| 3 | **Walk into Government Engineering College Thrissur** during lunch break (1 PM) | 60 min | ☐ |
| 4 | **Recruit 1 ambassador** from that visit | (within #3) | ☐ |
| 5 | **End-of-day check** — pull the metric, decide tomorrow | 5 min | ☐ |

**Why this order:** broadcasts cost you 10 min and start the clock — replies trickle in while you're doing #2 and #3. The poster is the slowest physical action (printing, taping). The college visit is best at lunch when students are out of class.

---

## Step 1 — Broadcast (now, 15 min)

Open WhatsApp Business → message each of these from your phone. **Send the same message verbatim to all 5.** Do not personalise — it slows you down and changes nothing.

The 5 phone numbers and the message will be in `broadcasts.md` (next iteration). For now, the message is:

```
Hi 👋 Quick update from Printosky.

You can now earn *Rs.20 store credit* every time
a friend places an order using your link.

Reply *MY CREDITS* to get your personal share link.

Project season + free credit = no excuse to share 😄
```

When they reply `MY CREDITS`, the bot does the rest — no action from you.

---

## Step 2 — Poster (after broadcast, 20 min)

Iteration 3 will give you a print-ready PDF. For now, if you're impatient: open `docs/REFERRAL_KIT.md` section "1. Counter poster" — the layout is there, drop into Word, print A4, tape up.

---

## Step 3 — College visit (1 PM, 60 min)

**Where:** Government Engineering College Thrissur (closest, biggest engineering student body).
**When:** 1:00 PM — students at canteen / outside classrooms during lunch.
**Who:** any final-year student who looks busy with a project (laptop bag, papers in hand). Engineering, B.Arch, MBA — all good.

**Pitch (memorise — 2 sentences):**

> "Hey — Printosky just launched a thing where every friend you bring earns you ₹20 off your next print. No app, no signup. Want me to send you the link?"

If they say yes → send `wa.me/919495706405?text=hi` to their number, walk them through getting their ref code (rate ★★★★★ on next job → bot sends share link).

If they say no → "Cool, no worries." Move on. You're looking for ONE yes.

---

## Step 4 — Ambassador (within step 3)

The first student who says "yes, I'll share with my class group" is your ambassador for this week. Take their phone number. That's it. No formal contract, no commitment ceremony. Just say:

> "If you can share it once in your class group today, I owe you a free print run. Deal?"

---

## Step 5 — End of day check (9 PM, 5 min)

Run this in a Bash terminal:

```bash
psql "$SUPABASE_URL" -c "SELECT COUNT(*) FROM bot_sessions WHERE referral_code IS NOT NULL;"
```

(Or whichever method you use — ask me to wire `metric-watch.py` if you want this automatic.)

- **0 → 0:** broadcasts went unread, college didn't share. Iterate tomorrow with different framing or different phones.
- **0 → 1:** funnel works. Tomorrow: do this again.
- **0 → ≥3:** funnel is hot. Send the next 5 broadcasts and visit a second college.

---

## What you are NOT doing today

- Not building features.
- Not designing new flows.
- Not answering "what about pdf-editor / pptx / alignment service" — those are next-week questions.
- Not perfecting the poster — taped paper beats Photoshopped vapor.

---

## When you finish (or get blocked)

Come back, tell me which checkboxes hit and what didn't. We adjust tomorrow's sprint based on actual data, not theory.
