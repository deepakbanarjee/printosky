# Printosky — Marketing Kickoff (7-Day Plan)
_Last updated: 2026-04-30_
_Companion: [GROWTH_PLAN.md](GROWTH_PLAN.md), [FEATURE_PIPELINE.md](FEATURE_PIPELINE.md)_

## Goal

Get **20 paying referrers** + **50 referred orders** in 7 days. Project season is the perfect window — students are running around for thesis/project printing right now.

## Single message to all channels

> *Printosky now pays you to share. Get ₹20 store credit per friend who orders.*
> *Send your file to wa.me/919495706405 — rate us 5⭐ and we'll send your personal share link.*

That's it. One sentence. Don't dilute.

---

## Day 1 — Tuesday (today)

### Counter (zero cost)
- [ ] Print **A4 poster** with the message above + QR to `wa.me/919495706405?text=hi`
- [ ] Pin on shop entrance + above the printer pickup counter
- [ ] Tell every walk-in: *"Rate us 5 stars and I'll send you a code that gives you ₹20 every time a friend orders"*

### WhatsApp broadcast (zero cost — Meta Cloud API: 1,000 free service messages/month)
- [ ] Send to all customers with a 4-5 star review in last 90 days:
  > Hi! Quick update from Printosky 👋
  > You can now earn **₹20 store credit** for every friend you refer.
  > Reply *MY CREDITS* to get your share link.
- [ ] Track: how many open, how many reply MY CREDITS

### Existing Instagram (zero cost)
- [ ] One Reel: "₹20 cashback per friend you refer to Printosky" — 15s, screen recording of the share-link flow
- [ ] One static post: poster image, swipe-up explanation
- [ ] Story: "Project season pricing recap" (use rate_card values)

---

## Day 2-3 — College WhatsApp groups

Target: **10 groups across 5 colleges** (per [GROWTH_PLAN.md](GROWTH_PLAN.md) target list).

For each group:
- [ ] Recruit 1 student ambassador per college (Government Engineering Thrissur, Vimala, St. Thomas, Christ Irinjalakuda, Vidya Academy)
- [ ] Give them their personal share link + a 60-second voice note explaining the offer
- [ ] Pay ₹500 Amazon voucher signup + ₹20/referred order (matches store credit, but in cash, since they need motivation)

Track via the unique `ref_CODE` per ambassador. Anything else is unmeasurable.

---

## Day 4-5 — Hostel push

Hostel wardens coordinate bulk student orders. They are the highest-leverage referrer.

- [ ] List 5 hostels within 3 km of shop
- [ ] Walk in with: A4 poster, list of ambassadors at the host college, 5 sample bound theses
- [ ] Pitch: *"You send students. We pick up + drop. Your hostel gets a 5% override on every order."*
- [ ] Set up `referrers` row per hostel warden with `platform=hostel`

---

## Day 6 — GMB push

Per [GROWTH_PLAN.md](GROWTH_PLAN.md) Phase A: Google My Business is the highest-ROI channel.

- [ ] Verify GMB profile is fully complete (photos, hours, services list, WhatsApp click-to-chat)
- [ ] Ask every customer who collected a job today for a Google review
- [ ] Target: 10 new reviews this day alone

---

## Day 7 — Measure & double down

End-of-week review questions (answer in [SPRINT_BACKLOG.md](../SPRINT_BACKLOG.md) or a new note):
1. How many `referrers` rows exist? (`SELECT COUNT(*) FROM referrers`)
2. How many `referral_credits` rows? Total ₹ logged?
3. Which platform delivered the most orders? (`SELECT platform, COUNT(*) FROM referrers JOIN referral_credits USING(...)`)
4. Which channel was loudest but converted least? Drop it.
5. Which ambassador converted best? Pay them more.

---

## Channels we are NOT spending on yet

Per [GROWTH_PLAN.md](GROWTH_PLAN.md) "what NOT to spend on during Phase A":

| Channel | Why not |
|---------|---------|
| Meta paid ads | CAC too high before retention is proven |
| Influencer marketing | Wrong audience for print services |
| Print pamphlets / flyers | No tracking |
| LinkedIn ads | B2C print is not a LinkedIn purchase |

Revisit at Phase B when we have signed franchise / institutional contracts.

---

## Brand voice reminders

- It's **Printosky**, not OSP. (Shop is OSP. Brand is Printosky. They are different things.)
- Per-page / per-sheet pricing — never bundle quotes.
- Use ₹ symbol or "Rs." consistently in customer-facing copy.
- Voice: direct, useful, no fluff. We are students' fastest path from messy file → bound thesis.

---

## Killer numbers to track weekly

| Metric | Where | Goal Month 1 |
|--------|-------|--------------|
| New referrers / week | `SELECT COUNT(*) FROM referrers WHERE created_at > now()-interval '7 days'` | 20 |
| Credits earned / week | `SELECT SUM(amount_inr) FROM referral_credits WHERE created_at > now()-interval '7 days' AND redeemed_at IS NULL` | ₹2,000 |
| GMB reviews | dashboard | +30 |
| Instagram followers | manual | 500 |
| Repeat customer rate | `SELECT phone, COUNT(*) FROM jobs GROUP BY phone HAVING COUNT(*) > 1` | 25% |
