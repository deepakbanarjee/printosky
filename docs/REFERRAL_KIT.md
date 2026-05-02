# Printosky Referral — Live Kit
_Last updated: 2026-05-02_
_Companion to: [MARKETING_KICKOFF.md](MARKETING_KICKOFF.md), [GROWTH_PLAN.md](GROWTH_PLAN.md)_

> **Status (2026-05-02):** End-to-end smoke test passed on production Supabase + Vercel. System is correct and idempotent. **Real users: 0.** No customer has clicked a ref link yet. This kit is the cure.

---

## Smoke test result

All 7 paths verified against production:

| Step | What was verified | Result |
|------|-------------------|--------|
| 1 | `_capture_referral_code` writes `bot_sessions.referral_code` | PASS |
| 2 | `_credit_referrer` inserts ₹20 row on payment | PASS |
| 3 | `MY CREDITS` returns correct unredeemed sum | PASS |
| 4 | `/referrals/leaderboard` aggregation matches | PASS |
| 5 | Duplicate credit on same `(code, order_id)` is skipped | PASS |
| 6 | `/referrals/redeem` atomic update on `redeemed_at IS NULL` | PASS |
| 7 | Already-redeemed credit cannot be re-redeemed (race-safe) | PASS |

Test rows cleaned up. State is back to seed: Deepak ₹40 balance, Anu ₹20 balance.

---

## 1. Counter poster (print today)

**Format:** A4 portrait. Tape next to the printer pickup counter and one on the front door.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       PRINTOSKY REWARDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Bring a friend.
  Earn ₹20 store credit.
  Every order. No limit.

  ★★★★★ rate us → get your
  personal share link → share
  with classmates → friends
  order → you save on yours.

  ━━━━━━━━━━━━━━━━━━━━━━━━━

  [QR CODE]
  Scan to chat with us

  wa.me/919495706405

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       Oxygen Students Paradise
                Thrissur
```

**QR target:** `https://wa.me/919495706405?text=hi`

**Build the QR with any free generator** (qr-code-generator.com, no signup needed). Print at A4. Tape it up. Done.

---

## 2. WhatsApp broadcast — first 5 customers

These are the highest-engagement repeat customers from your jobs database (last 60 days, sorted by job count). Send them this message **manually from your business WhatsApp** today:

| Phone | Jobs | Last seen | Notes |
|---|---|---|---|
| `918089699436` | 27 | 2026-03-27 | Whale customer — talk to them first |
| `917715939903` | 9 | 2026-03-18 | Repeat regular |
| `917300073000` | 7 | 2026-03-25 | Repeat regular |
| `919947688696` | 5 | 2026-03-26 | Repeat regular |
| `918532073688` | 5 | 2026-03-18 | Repeat regular |

### Broadcast message

```
Hi 👋 Quick update from Printosky.

You can now earn *Rs.20 store credit* every time
a friend places an order using your link.

Reply *MY CREDITS* to get your personal share link.

Project season + free credit = no excuse to share 😄
```

That's it. Don't add disclaimers, terms, or a sign-off — they'll skip past it.

When they reply `MY CREDITS`, the bot auto-creates their referrer row, generates their unique code, and sends the share link. **No staff action needed for that flow.**

---

## 3. Five more customers, then ten

After 24 hours of sending the first 5, send to these next:

| Phone | Jobs |
|---|---|
| `916238007632` | 2 (most recent active) |
| `919048487775` | 2 |
| `918111934925` | 2 |
| `918592925551` | 3 |
| `919632005703` | 3 |

Then the rest of the 30+ list from `jobs` table. Pace it across 3-4 days so a) you can answer questions, and b) Meta doesn't flag you for spam.

---

## 4. College ambassador pitch

Walk into one college today. Government Engineering Thrissur is closest. Find one student doing a project. Say:

> "We made a thing where every friend you bring to Printosky gets you ₹20 off your next print. No app, no signup. Want me to send you a link? You can share it in your class group. Worst case, nothing happens. Best case, you cover your own thesis printing."

If they say yes: send them `wa.me/919495706405?text=hi`, walk them through getting their ref code (rate ★★★★★ on their next job → they get the share link).

You don't need 10 ambassadors. **You need 1 who actually shares.** Find that one this week.

---

## 5. Single metric to track

Forget vanity numbers. The only number that matters in the next 7 days:

```sql
SELECT COUNT(*) FROM bot_sessions WHERE referral_code IS NOT NULL;
```

This is the count of **real people who clicked a ref link**. Today: 0. Goal in 7 days: ≥10.

If you hit 10 in 7 days, the funnel works → spend more on it. If you hit 0, the funnel doesn't work → stop building features and go ask 5 customers why.

---

## 6. What NOT to do this week

- Don't build TOP REFERRERS bot reply yet → you have 0 referrers
- Don't build the manual credit-adjust UI yet → no one's earned credits
- Don't run paid ads → CAC is unknown
- Don't worry about leaderboard polish → the leaderboard has 2 fake rows

Build comes after demand. Ship the poster + broadcast + ambassador this week.

---

## What's blocking actual launch

Per the smoke test, **nothing technical is blocking.** The only blocker is the physical/operational push:

- [ ] Print the poster (15 min)
- [ ] Send the broadcast to top 5 (10 min)
- [ ] Walk into one college (60 min)
- [ ] Recruit 1 ambassador (yes/no in same hour)

Two hours of work. Do those, then come back.
