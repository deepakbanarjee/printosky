# Printosky — Expansion Plan
_Last updated: 2026-04-29_

---

## Vision
Single print shop → cloud-native franchise platform → print marketplace.
Every phase funds the next. No external capital until Phase C.

---

## Phase A — Single Owner, Cloud-Native (Now → Month 12)

### Goal
Make the Thrissur shop bulletproof. Eliminate store PC as a server.
Prove unit economics. Manually onboard 2-5 trusted franchise shops.

### The Agent Architecture (core of Phase A)
Store PC becomes a dumb agent (~150 lines). All logic moves to cloud.

```
STORE PC AGENT (agent.py ~150 lines)
  ├── Watchdog: C:\Printosky\Jobs\Incoming\
  ├── New file → upload Supabase Storage → POST /api/jobs/register on Vercel
  └── Supabase Realtime listener → on "print" event → SumatraPDF → printer

CLOUD (all logic, all state)
  ├── Vercel (api/index.py): WhatsApp bot, pricing, payments, staff auth, academic API
  ├── Supabase: Postgres (primary DB), Storage (incoming files), Realtime (print dispatch)
  └── Netlify: admin.html dashboard
```

**Franchise install = agent.py + .env + pm2 start. 30 minutes. Nothing else.**

### A-Sprint Roadmap

| Sprint | Work | Outcome |
|--------|------|---------|
| A1 (Week 1-2) | PM2, UptimeRobot, Vercel env vars, Supabase pin_salt migration | Shop never goes dark unnoticed |
| A2 (Week 3-6) | Finish Meta Cloud API cutover; move webhook_receiver to Vercel; Supabase as primary write | No store PC web requests |
| A3 (Week 7-9) | Build agent.py; retire watcher.py + print_server.py + webhook_receiver.py | 6,000 lines → 150 lines on store PC |
| A4 (Week 10-12) | shop_id on all tables; per-shop config table; manual provisioning script | Second shop live in 30 min |

### A → B Trigger (all three must be true)
- [ ] 3+ shops live and profitable
- [ ] Agent handling all printing — no store PC web server
- [ ] Supabase is primary — SQLite is offline cache only

---

## Phase B — Franchise SaaS (Month 12 → Month 24)

### Goal
20-50 shops onboard themselves. Shops pay ₹1,500-3,000/month.
B revenue funds Phase C infrastructure and capital raise.

### What gets added over Phase A
- Self-serve onboarding: signup → WABA via Meta Embedded Signup → Razorpay connect → agent download → live
- Per-shop billing via Razorpay Subscriptions
- Supabase RLS policies enforcing strict tenant isolation
- Per-shop subdomains via Vercel rewrites (`acmeprint.printosky.com`)
- Inngest replaces supabase_sync.py polling — event-driven job flow
- B2B academic partnerships: colleges sign institutional contracts
- Mobile-friendly PWA upgrade to admin.html
- Automated provisioning script: create tenant → generate .env → send to shop

### Unit Economics Target
| Metric | Target |
|--------|--------|
| Shops | 50 |
| ARPU | ₹2,000/month |
| Monthly revenue | ₹1,00,000 |
| Infra cost | ~₹2,50,000/month |
| Gross margin | ~70% |

### B → C Trigger (all three must be true)
- [ ] 20+ shops with >80% paying past month 3
- [ ] Academic feature proven with signed institutional contracts
- [ ] ₹1 Cr+ ARR or clear 6-month path to it

---

## Phase C — Print Marketplace (Month 24+, raise capital)

### Goal
Customers post print jobs → nearby shops accept → pay → pickup or delivery.
Take rate 8-15% via Razorpay Route. B's shop network = ready supply side.

### What gets added over Phase B
- Customer-facing PWA + WhatsApp Flows for in-thread checkout
- PostGIS on Supabase for geospatial shop matching (nearest 3-5 shops)
- Razorpay Route: shop gets 85-92%, platform gets 8-15% — split at payment time
- Delivery: Porter API + Shadowfax for last-mile within 3 km
- KYC for shops: Bureau.id / HyperVerge
- TDS 194-O compliance (1% TDS on every shop payout — mandatory at this scale)
- Trust layer: ratings, SLA enforcement, dispute resolution, refund flow
- Campus ambassador program

### Why raise capital at C, not earlier
- Cold start: need 30 active shops per city before customer app works → B builds that
- CAC at marketplace scale (₹150-400/customer) requires capital → B revenue seeds it
- Investors fund proven supply side → B is that proof

---

## Target Markets — Expansion Geography

Enter markets in order of college density + existing customer signal.
**Rule:** Only enter when (a) existing customer can anchor first shop, or (b) college partnership is pre-signed.

| Priority | Market | Rationale |
|----------|--------|-----------|
| 1 | Thrissur | Home base, proven unit economics |
| 2 | Kochi | Largest Kerala city, 50+ colleges, tech park demand |
| 3 | Kozhikode | University town, NIT + Calicut University cluster |
| 4 | Trivandrum | Engineering + government college hub |
| 5 | Coimbatore | Tamil Nadu entry, 100+ engineering colleges |
| 6 | Bangalore (university areas) | Scale market, highest volume ceiling |

---

## Infrastructure Cost by Phase

| Phase | Shops | Monthly Infra | Revenue Target |
|-------|-------|---------------|----------------|
| A | 1-5 | ₹8K-15K | ₹20K-50K |
| B | 5-50 | ₹2L-2.5L | ₹1L-10L |
| C | 50-1,000 | ₹25L-40L | ₹2.5Cr+ |

---

## Related Documents
- Technical architecture → [ARCHITECTURE.md](ARCHITECTURE.md)
- Security posture → [SECURITY.md](SECURITY.md)
- Customer growth strategy → [GROWTH_PLAN.md](GROWTH_PLAN.md)
- Feature backlog → [../SPRINT_BACKLOG.md](../SPRINT_BACKLOG.md)
- Master operational plan → [MASTER_PLAN.md](MASTER_PLAN.md)
