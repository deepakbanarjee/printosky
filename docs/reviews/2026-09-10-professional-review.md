# Printosky — Code, Architecture and Operations Review

**Prepared for:** Deepak Banarjee  
**Review date:** 10 September 2026  
**Repository:** deepakbanarjee/printosky, `main`  
**Reviewed commit:** `3df7b7e9131bad896b3fa6c2399a89c83f5deb67`  
**Status:** Analysis and recommendations only. No repository code, configuration, database, deployment, or business records changed.

## 1. Executive assessment

Printosky has a valuable foundation: it reflects how a real print shop works, including local printers, student documents, mixed-colour jobs, finishing, pickup, staff activity and unreliable connections. Its strongest asset is this accumulated operational knowledge. The existing system is worth improving incrementally.

However, I would not approve expansion of unattended printing or online payment processing on the strength of the current tests alone. This review reproduced an authentication bypass, incorrect cloud batch-payment allocation, and a pricing-error path that accepts a zero-value quote. It also found unsafe failure boundaries around payment processing, partial print retries, and cloud/local synchronization.

**The recommended direction is a controlled hybrid system:** one authoritative order and payment model in the cloud, a dependable Windows print agent in each shop, local continuity for counter work, and a staff queue that highlights exceptions and deadlines. Keep the existing pricing and PDF expertise. Improve reliability and accountability before adding more product areas or introducing a new framework.

“Optimal” here means the best-supported design for the current small multi-location operation. It is not a mathematically proven optimum: current order volumes, actual staff handling time, printer utilization, expenses, margins, and live deployment settings were not available. A measured two-week operating baseline is part of the recommendation.

## 2. Scope and evidence standard

The review inspected the repository tree, project rules, current architecture and acceptance notes, cloud and local payment paths, authentication, order creation, pricing boundaries, print planning and dispatch, coordination, synchronization, routing, selected tests, and deployment workflows. It researched official documentation from PrintNode, PaperCut, Gelato, Printavo and Printo.

This is a targeted architecture and code review, not an exhaustive line-by-line security audit. No live exploit attempt, real payment, customer message, printer job, production database query, or configuration change was performed.

Evidence labels used throughout:

| Label | Meaning |
|---|---|
| Reproduced | The current function was executed locally with synthetic data and external services replaced by mocks. |
| Code-verified | The behavior or risk follows from the reviewed implementation; no live incident is claimed. |
| Workflow-verified | GitHub run metadata or step results were read for the reviewed commit. |
| Documented history | Repository records describe a past incident or acceptance result; it was not independently witnessed. |
| Unverified | Requires production settings, logs, actual devices or business measurements. |
| Recommendation | Proposed future behavior, not a claim that it already exists. |

The local checkout matched the reviewed remote commit and began clean. Project instructions were read from `CLAUDE.md` and `.agents/AGENTS.md`. Their safeguards include fail-loud behavior, preserving known-good PDF geometry, and special protection for the Konica duplex/simplex workaround. These remain constraints on any subsequent implementation. [C01]

## 3. What you have done well

### 3.1 Strong understanding of the actual business

The system accounts for details generic ecommerce software often misses: paper size, copies, duplex, colour pages, imposition, binding, physical item receipt, inter-store finishing, photocopy billing and pickup. `rate_card.py`, `print_planner.py`, `service_jobs.py` and the service handlers contain meaningful business logic. Preserve this knowledge rather than recreating it in a new stack. [C02]

### 3.2 Sensible separation between cloud access and physical printing

The website and cloud API can receive work independently of a particular counter PC. Windows software remains close to the printer drivers and local files. Local counter printing exists alongside cloud assignment. This is a sound architectural direction for equipment-dependent operations. [C03]

### 3.3 Useful coordination and recovery mechanisms

`device_lease.py` implements atomic claims and role leases. `store_puller.py` adds realtime wake-ups, a polling fallback, retry pacing and stale-claim recovery. These address real failure modes and are more mature than simply polling a folder and hoping one machine prints. The remaining issue is the boundary between a claimed job and physical output, discussed below. [C04]

### 3.4 Printing rules have been tested on paper

The project records real Konica simplex/duplex and scaling verification, including the dual Windows-queue workaround. This matters: a software test cannot prove that a driver obeys a print option. The September acceptance record also distinguishes completed checks from pending ones. [C05]

### 3.5 A substantial automated test investment

The `tests/` tree contains **131 test files**. GitHub reports a successful `test` workflow for the reviewed commit. Tests cover pricing, PDF geometry, claims, webhook behavior, services and known regressions. This is a meaningful strength, although file count is not coverage and green CI is not proof that all customer workflows are correct. [C06]

### 3.6 Security improvements already exist

The implementation includes salted PBKDF2 staff PIN verification, signature checks for payment and Meta webhooks, authenticated local operations, and migrations tightening several RLS policies. The problem is inconsistent enforcement across alternative paths, rather than a complete absence of security controls. [C07]

### 3.7 Good direction in monitoring

`ops_watchdog.py` centralizes failure and recovery alerts. Version reporting and realtime-delivery monitoring recognize that an alive process may still be doing no useful work. These mechanisms should become mandatory at each critical boundary. [C08]

### 3.8 Existing durable AI processing is worth reusing

`api/inngest.py` already separates document-processing work from the main request handler and includes chunked steps. There is no need to introduce another orchestration platform merely to obtain background jobs. First establish consistent persistence and retry rules around the system already present. [C09]

### 3.9 Operational learning is recorded honestly

The repository documents failures, regression causes and untested hardware combinations. That is a strong practice. The next improvement is making this evidence authoritative and current so older documents cannot contradict the implementation.

## 4. Confirmed defects and high-priority weaknesses

Priority: **P0** = address before expanding affected public/financial automation; **P1** = stabilize before scaling; **P2** = improve after core controls are reliable. No changes are authorized by this report.

### F01 — Legacy authentication accepts incorrect passwords

**Priority P0 · Reproduced · Critical**

`api/index.py::_handle_auth_legacy` returns `200`, `ok: true`, and calls `_mint_supabase_jwt()` for any non-empty password after the optional hash comparison. This also happens when a configured hash does not match. The router exposes this handler through legacy/auth login paths; `vercel.json` routes the legacy function path and `/auth/*` to the Python API. [C10]

The local reproduction used an intentionally incorrect password and a non-matching configured hash. Result: `200`, `ok: true`, and the mock JWT. No real JWT was requested.

**Business consequence:** unauthorized entry into the authenticated data path is possible if JWT minting is configured in the deployed environment. The exact records accessible depend on live RLS. This is not evidence that someone has accessed customer data.

**Recommendation:** reject every unmatched credential; consolidate the Netlify and Python authentication contracts; issue individual staff sessions with explicit roles and store scopes. Add behavioral tests for wrong password, missing configuration, inactive staff, revoked sessions and cross-store access. Validate the fix in staging before deployment.

### F02 — Cloud batch payments overstate job-level collections

**Priority P0 · Reproduced · High**

`_process_razorpay_payment` loops over a batch and calls `update_job_paid(jid, amount, method, pay_id)` with the **entire batch amount for each job**. `db_cloud.update_job_paid` writes that amount to `amount_collected`. [C11]

Local reproduction: one ₹100 payment covering J1 and J2 produced two calls recording ₹100 each. Summed job collections become ₹200. With n jobs, this branch can represent n times the payment.

The repository already has allocation tests for `webhook_receiver.py`, the old store-PC receiver. Those do not protect this different cloud implementation. This is a concrete example of an old fix failing to cover the currently deployed path. [C12]

**Recommendation:** record one payment in a payment ledger, then allocate integer paise across order lines using a documented rule. Allocations must sum exactly to the payment. Test both cloud entry points and shared financial logic; retire redundant implementations only after call sites are verified.

### F03 — Payment acknowledgement and persistence are not one reliable operation

**Priority P0 · Code-verified · High**

The Razorpay HTTP route writes `200 OK` before calling the payment processor. `_mark_webhook_processed` records a deduplication marker before the business updates; on database errors it allows processing to continue. The service-payment path similarly marks a payment ID before updating the job. [C13]

Two failure modes follow:

1. A marker succeeds but the payment update fails: a subsequent delivery may be rejected as already processed, although business state is incomplete.
2. Deduplication storage fails but processing proceeds: duplicate events can produce repeated side effects.

The service handler does alert on a failed write, which is good, but an alert is not durable recovery. It also calculates an accumulated total from a previously read job and overwrites the total, leaving a lost-update risk when distinct payments arrive concurrently.

**Recommendation:** verify signatures, durably insert an event into an inbox, acknowledge only after that insertion succeeds, and process it with retryable status. In a database transaction, insert the unique payment, allocate it, update balances and create outgoing notification events. Use payment identity as well as event identity. Preserve production status when delayed payment events arrive. Reconcile gateway transactions independently.

### F04 — Pricing failure becomes an accepted ₹0 quote

**Priority P0 · Reproduced · High**

`api/handlers_order.py::_handle_order_create` catches a pricing exception and assigns `total = 0.0`, then creates the job and sends a confirmation. A local mock pricing exception reproduced a successful response and a persisted zero quote. [C14]

**Business consequence:** a broken rate calculation can look like a legitimate free order. The observed behavior does not prove that an unpaid zero-value order is automatically printed.

**Recommendation:** return a clear pricing-unavailable response or create an explicitly unpriced draft requiring staff approval. Never convert an exception into a commercial price. Validate copies, page selections, colour-page membership and supported layout values against the actual uploaded document; the current handler relies on client-supplied page metadata.

### F05 — Partial print failure can repeat already submitted sections

**Priority P1 · Code-verified · High**

`store_puller.auto_print` sends a sequence of sub-jobs and marks the order printed only after the last succeeds. This correctly avoids declaring a partial order complete. However, a later-section failure returns false; `pull_once` leaves the job unrecorded and retries the whole plan later. There is no persisted per-action completion checkpoint in this path. [C15]

Example: sections A and B are submitted; section C fails. The next attempt can submit A and B again. The code comment suggesting manual attention does not change the actual automatic retry behavior.

**Recommendation:** persist print attempts and action IDs, with file/spec hashes and spool identifiers. Retry only actions known not to have been submitted. When a timeout or crash leaves physical output uncertain, hold the job for operator reconciliation rather than automatically printing it again.

### F06 — Atomic claims do not guarantee exactly-once physical output

**Priority P1 · Code-verified · High**

The atomic conditional update prevents simultaneous acquisition of the same available claim. It does not make a printer side effect transactional with the database. Claims expire after a default 900 seconds; this print path has no per-job claim-renewal/fencing protocol. Startup also releases claims held by the same device from a previous run. [C04]

`send_to_printer` treats a successful SumatraPDF process exit as grounds to mark `Printed`. This establishes successful command submission, not verified finished sheets. The local status update can fail and return without raising; cloud status propagation runs in a daemon thread. [C16]

**Recommendation:** distinguish queued, claimed, spooled, output-confirmed, finishing, ready and delivered. Persist attempt ownership with renewable leases and attempt tokens. A crash after spooling must produce an uncertain-output state. Retain printer evidence or staff confirmation for the irreversible boundary. Describe the guarantee accurately as duplicate-resistant dispatch with explicit uncertain-output handling.

### F07 — Sync failures can masquerade as empty successful syncs

**Priority P1 · Reproduced collector behavior; code-verified consequence · High**

`collect_jobs` logs database exceptions and returns `[]`. A missing jobs table reproduced that result locally. `sync_once` treats an empty collection as success for that table and may report overall success if the other collections are empty or succeed. Thus “nothing to sync” and “could not read the source” are indistinguishable. [C17]

It also repeatedly pushes the latest 500 jobs, including status and payment fields, with merge-upserts. An older changed job can fall outside that window, and a stale local row can overwrite cloud-owned fields if the same job exists in both places. There is no version/conflict check in this upsert path. These are architectural risks, not measured instances of corruption.

**Recommendation:** return typed collection results, make failed reads unhealthy, use a local durable outbox/change cursor, and define field ownership. Cloud payment events should own financial state; the agent should publish print and counter events. Synchronization is not a substitute for backups.

### F08 — File trust boundary is too broad

**Priority P1 · Code-verified · High**

The public order-creation handler accepts any non-empty `file_url`; the store puller later downloads the supplied URL with `requests.get`. No origin allowlist or upload-ownership binding appears in that path. The downloader reads the response into memory without an explicit size cap. [C14], [C18]

This creates a server-side request forgery and resource-exhaustion risk, especially because the downloader runs on the shop network. Actual exploitation would require the job to reach a pullable paid state; this review did not attempt it.

Several upload functions return public storage URLs, including payment-proof handling. The code therefore assumes link-accessible files; actual bucket visibility and grants were not inspected. [C19]

**Recommendation:** accept an owned storage object ID rather than an arbitrary URL; enforce approved HTTPS origins, redirect and address checks, byte limits and file-type validation. Use private buckets with short-lived authorized download URLs for customer documents and proofs. Introduce retention rules with customer opt-in for longer reorder storage.

### F09 — Staff identity and authorization need consolidation

**Priority P1 · Code-verified · High**

`_mint_supabase_jwt` signs into one shared Supabase Auth account. PIN logins therefore do not automatically create distinct database identities. The academic student helper compares a supplied phone string with the stored phone; this is knowledge of an identifier, not proof of possession. The cloud staff-login handler does not show the rate limiter applied to that route. [C20]

The browser stores a store token in localStorage, and `netlify.toml` has no CSP declaration. These increase the consequences of an XSS flaw, but this review did not prove a working XSS exploit. Broad CORS alone is not an authentication bypass.

**Recommendation:** individual identities, explicit owner/manager/counter/production roles, store-scoped authorization, short-lived sessions, session revocation and durable login throttling. Use verified customer sessions or scoped order-access tokens. Move cloud sessions to secure HttpOnly cookies where the architecture supports them; handle CSRF and the separate LAN-agent origin deliberately.

### F10 — Green workflow does not mean schema verification ran

**Priority P1 · Workflow-verified · High assurance gap**

GitHub run `34397657051` is successful, but `Checkout`, `Set up Python`, `Install deps`, and **Check schema drift** are all skipped. The YAML explicitly skips live verification when the database connection secret is absent. [C21]

**Recommendation:** display “not verified” prominently and make required release gates reject a missing verification prerequisite. Keep liveness, configuration presence, database reachability, data freshness, and successful business processing as separate signals.

### F11 — Routing inputs do not match the intended capability model

**Priority P1 before enabling broader routing · Code-verified · Conditional**

The multi-store payment branch creates `JobSpec(job_id=job_id)` without populating colour, paper size, finishing or customer coordinates. The defaults are B&W/A4 with no finishing. This can defeat capability filtering when `MULTISTORE_ROUTING_ENABLED` is enabled. Separately, `decide()` defaults to UTC while `_is_open()` compares the supplied hour directly to configured opening-hour integers. Local-time semantics need to be explicit. [C22]

**Recommendation:** build routing input from the immutable paid print specification, honor the customer's pickup choice, compare opening times in each store's timezone and route by estimated machine/finishing minutes. Do not enable expansion routing until these contracts are tested.

### F12 — Large modules and parallel implementations increase regression risk

**Priority P2 · Code-verified**

| File | Lines in reviewed snapshot |
|---|---:|
| `api/index.py` | 4,091 |
| `print_server.py` | 3,693 |
| `db_cloud.py` | 3,106 |
| `watcher.py` | 1,478 |
| `website/admin.html` | 7,616 |
| `website/jobs.html` | 4,525 |

Line counts are not quality scores. Here they matter because authentication, commerce, orchestration and presentation are spread across large files and alternative paths. The batch bug demonstrates a real maintenance consequence. Handler extraction has started, but some handlers import response utilities from `api.index`, preserving coupling.

**Recommendation:** extract shared domain logic and infrastructure adapters incrementally. Establish a single payment service and authorization layer first. Move browser behavior to reusable modules and a common operator shell. A React/Next.js rewrite is not a prerequisite and would not itself fix these defects.

## 5. Your documented failures — and the management lessons

These are failures of system design or operating controls, not judgments about your ability. Historical records must not be mistaken for current outages.

| Repository-documented event | Consequence described in repository | Lesson |
|---|---|---|
| Nattika pipeline failed behind several silent layers | Seven days without an effective alert | Health must prove useful output and freshness, not just process existence. |
| Multiple boxes imported printer history independently | 388 duplicated printer-job rows | Shared work needs ownership and unique source identities. |
| September 8 retry feedback loop | Approximately once-per-second retries; process died with stale claim; paid work remained blocked | Test failure feedback loops and recovery, not only normal completion. Current code adds pacing and recovery. |
| September acceptance found UI/rate-card mismatch | Five service kinds supplied quantities the pricing function did not read | Contract tests must use the actual UI payload. |
| MIS appeared plausible while showing old periods | Acceptance notes describe February–March data appearing for months | Every report needs date range, source, freshness and reconciliation evidence. |
| Proof tooling reported success over no successful builds | False reassurance before spending paper | A passing check must require nonzero expected evidence. |
| Store PCs and cloud deploy independently | A merged fix may not be running in the shop | Every release needs a per-machine version/acceptance record. |

Sources: project agent rules, coordination migration, retry tests and the September acceptance record. [C01], [C04], [C05], [C23]

**The recurring pattern:** implementation completion has sometimes been treated as operational completion. A feature is complete only when its actual entry point, deployed version, real dependencies, failure behavior, staff recovery and accounting outcome are verified together.

## 6. Comparable platforms and what to learn

Only official product/API documentation supports the technical claims below. Vendor feature descriptions are not independent performance benchmarks. Where internal technology is undisclosed, it remains unknown; this report does not guess React, Laravel, AWS, Kubernetes or database vendors from page appearance.

| Platform | Verified technology/architecture or workflow | Comparison and recommendation |
|---|---|---|
| **PrintNode** | HTTPS/JSON printing API plus installed client; printer capabilities, job-state APIs and request idempotency. Its documented idempotency-key retention is 24 hours. | Closest infrastructure comparison. Printosky's local agent is a reasonable choice. Borrow stable action IDs and state tracking. Consider a limited PrintNode pilot only if maintaining dispatch becomes more expensive than the service; first prove Konica queue, scaling and mixed-job fidelity. [E01] |
| **PaperCut Hive** | Cloud coordination with Windows/macOS edge nodes; edge processing, encryption and job replication. Printer discovery/monitoring uses local information including SNMP. | Validates hybrid architecture and device-aware monitoring. Borrow resilient local execution and secure release concepts. Hive is organizational print management, not a direct replacement for student quoting and retail finishing. [E02] |
| **Gelato** | REST/JSON order API, API-key authentication, order/item states, split orders, production holds and status webhooks. | Useful model for future partner fulfillment: keep one customer order with separate production tasks and explicit holds. Do not reproduce its global network complexity for two shops. Internal backend language/database not established here. [E03], [E04] |
| **Printavo** | Cloud shop software with quoting, customer proofs, production scheduling, station/staff tasks, capacity planning and reporting. An API documentation link exists, but its specification could not be retrieved in this review. | Strong workflow benchmark. Borrow an actionable production board, approval records and deadline-based scheduling. Its advertised core is decorated apparel, so local document-printing fit and integrations require validation before purchase. Internal stack unverified. [E05] |
| **Printo** | Indian online and retail printing, uploaded designs/templates, document printing and binding; express delivery is offered only for selected products/cities. | Stronger direct commercial comparison. Build simple, bounded service promises and a quick reorder experience. Its internal application architecture is not publicly established by the reviewed page. [E06] |

### Build versus buy

| Option | Main benefit | Main drawback | Assessment |
|---|---|---|---|
| Stabilize current hybrid stack | Preserves printer knowledge and business workflows; least disruption | Requires ownership of reliability and security | **Recommended now** |
| Keep Printosky commerce; pilot managed print transport | May reduce local dispatch maintenance | Additional service cost; hardware behavior still needs proving | Conditional experiment after baseline controls |
| Replace operations with a print MIS | Mature scheduling and reporting may reduce custom development | Migration, Indian payments and document-printing fit may be difficult | Obtain workflow demos only if operational fit is compelling |
| Full custom rewrite/microservices | Cleaner boundaries are possible | High migration risk; duplicates years of operational learning | Not justified by current evidence |

## 7. Recommended target architecture

Use a **modular application with a durable worker and store agents**. Keep deployment units few and domain responsibilities clear.

| Component | Recommended responsibility |
|---|---|
| Customer web/WhatsApp entry | Capture intent; show options, validated quotes, proof and pickup promise. Both create the same order model. |
| Cloud API | Individual authentication, authorization, order lifecycle, authoritative quoting and payment endpoints. |
| PostgreSQL/Supabase | Authoritative orders, payment ledger, allocations, fulfillment tasks, inbox/outbox events and audit trail. |
| Private object storage | Immutable source documents, approved print PDFs and proofs; scoped access and retention. |
| Durable worker | File preflight/conversion, notification retries, reconciliation and AI processing. Reuse the existing Inngest investment where appropriate. |
| Windows agent per store | Durable local queue, printer capabilities, document hash checks, spool submission and output evidence. |
| Local SQLite | Counter continuity and outbound events during outages; not an unrestricted second writer of cloud financial state. |
| Staff console | One queue of due work, exceptions, finishing, collection and payment discrepancies with role-appropriate actions. |

### Data contracts that matter more than framework choice

1. **One order, many items and tasks.** A multi-file customer order can create multiple print/finishing tasks without duplicating its payment.
2. **One immutable accepted specification.** Store page range, colour choice, copies, finishing, document hash, quote version and approval time together.
3. **Separate payment and production states.** A payment retry must not change Printed/Ready/Delivered back to Paid.
4. **Unique external payment identity.** Store provider, account scope, provider payment ID, amount in paise, currency and status. Keep reversals/refunds as auditable events.
5. **Durable inbox/outbox.** Commit business changes and outgoing events together; retry delivery without repeating business effects.
6. **Print attempt identity.** Each sub-job has its own status and spool evidence. Reprints require an explicit reason and link to the original attempt.
7. **Field ownership and version checks.** Local counter events cannot overwrite verified online collections; conflicts must enter a visible review queue.
8. **Capability-based routing.** Paper, colour, finishing, queue time, health and pickup commitment are explicit constraints.

Keep Netlify and Vercel initially if they work economically. Consolidating hosting may later simplify authentication and deployment, but provider count is not the principal defect. Do not introduce Kubernetes, a second message broker, a new database, or AI scheduling without a measured requirement.

Supabase documents that service keys can bypass RLS. Store PCs should therefore move toward narrowly scoped per-device authorization rather than broad service-role access. Live grants and policies must be checked before claiming tenant isolation. [E07]

## 8. The best-supported operating model for Printosky

### 8.1 Three work lanes with one order record

| Lane | Work | Default handling |
|---|---|---|
| Express | Valid PDF, standard paper/layout, confirmed price/payment, no unusual finishing | Automatic preflight and dispatch; staff checks output and completes handover. |
| Assisted | Office documents, uncertain colour, special scaling, document corrections | Operator reviews conversion/proof and obtains approval before printing. |
| Scheduled production | Large jobs, project binding, multi-step or inter-store work | Due-time and capacity scheduling; explicit task owner and finishing/transport steps. |

Automation eligibility must depend on verified file and device conditions. A questionable job moves to Assisted with a reason; it should not repeatedly retry in the Express lane.

### 8.2 Customer journey

| Stage | Recommended behavior | Staff effort reduced |
|---|---|---|
| Intake | QR/web link or WhatsApp creates one order; avoid collecting the same details twice | Less retyping and lost files |
| Preflight | Validate file readability, page count, size, orientation and requested colour | Fewer corrections at the printer |
| Quote | Server-calculated total, finishing, collection time and versioned approval | Fewer pricing disputes |
| Payment | Verified gateway event, or recorded counter cash/UPI; explicit deposit/balance | Less manual payment searching |
| Production | Assign the eligible machine and show status/exception | Less polling of chats and folders |
| Finishing | Task owner, service, due time and receipt/return tracking | Fewer forgotten binding jobs |
| Quality check | Confirm pages, sides, colour, copies, sequence and finishing | Fewer customer reprints |
| Collection | Pickup code/QR; balance check and handover timestamp | Faster counter service |
| Reorder | Reuse approved options and retained file with consent; reprice against current rates | Less repeat setup |

### 8.3 Coordinate Thriprayar and Nattika by total completion cost

The repository describes a B&W Konica at OSP and Epson capability at Nattika, with inter-store finishing workflows. Start with those documented capabilities and verify current device configuration onsite. [C03], [C05]

Use the Konica for suitable B&W volume when capacity and pickup timing support it. Keep short urgent jobs near their pickup location when transport would erase machine savings. Send finishing to the store that can complete it reliably, with a transfer manifest and a receiving acknowledgement.

**Routing rule:** choose the eligible route with the lowest expected production + staff + transfer + rework cost that still meets the promised collection time. Estimate workload in minutes, not just number of jobs: a 500-page bound order and a 2-page print are not equivalent queue entries.

Mixed-colour splitting needs an explicit economic check. Printing B&W on the Konica may lower click costs but add collation time. Preserve tested order/collation behavior; allow a whole-job colour-device option when it is cheaper overall or less error-prone.

### 8.4 Daily responsibilities

| Time | Owner | Essential checks |
|---|---|---|
| Opening | Shift lead | Device versions, agent heartbeat, last successful sync, printer readiness, paper/toner, carried-over exceptions |
| During work | Production owner | Oldest paid-but-not-started job, blocked output, finishing due, uncollected ready orders |
| Before transfer | Sending/receiving staff | Count packages/items; identify order and finishing service; record both handoffs |
| Closing | Cashier/manager | Cash drawer, verified UPI/gateway receipts, deposits, refunds, credits and outstanding balances |
| Daily review | Owner | Exceptions and unexplained variances; whether promised collection times were met |
| Weekly | Owner + technical maintainer | Reprint reasons, staff minutes/job, release regressions, restore evidence and highest-value backlog items |

The operator console should present “needs action” first. Management charts belong behind it. Staff should not need to infer a failure from a log line or an empty table.

## 9. Measurement and economics

No verified current savings or profitability figures are available. Use these as **proposed targets and measurement rules**, not claims about current performance.

| Metric | Definition | Initial operating target |
|---|---|---|
| Paid-to-queue latency | Verified payment to agent acknowledgement | p95 below 60 seconds during open hours for standard online jobs; validate load first |
| Missed realtime fallback | Delay when subscription delivery fails | Initially target reconciliation within 60–120 seconds; budget query load and backoff |
| Unexplained payment variance | Receipts minus ledger/allocations after reconciliation | Zero unexplained balance at daily close |
| Duplicate automation output | Repeated print action without an authorized reprint | Zero; uncertain output always held for review |
| On-time completion | Orders ready by their promised time / eligible orders | Establish baseline, then target at least 95% |
| Manual handling | Active staff minutes per order, split by lane | Reduce against the two-week baseline without raising reprints |
| Rework | Reprint sheets and staff time by reason | Downward weekly trend; distinguish machine, file and operator causes |
| Sync health | Last successful collection and acknowledgement per data source | Never green if a required source was unreadable |
| Restore readiness | Successful recovery from a selected backup | Demonstrated before expansion, repeated periodically |

**Contribution per order** = collected revenue − paper − ink/click cost − finishing material/vendor cost − payment charges − transfer/delivery − variable staff time − expected rework − attributable AI cost.

**Automation value per month** = orders/month × minutes saved/order ÷ 60 × loaded staff cost/hour, plus measured reduction in waste and errors, minus added software/support cost.

Illustration only: 100 jobs/day × 2 minutes saved = 200 minutes/day, or 3 hours 20 minutes of staff capacity. That is not a forecast for Printosky and is not necessarily a cash saving unless staffing or throughput changes.

Payment-gateway selection should follow a common ledger contract. A Cashfree change would not by itself fix the batch, deduplication or sync problems. Cashfree's own documentation requires unique payment tracking and validation of successful terminal payment events. Compare actual contracted fees, settlements, refunds, failure recovery and support separately; do not select a provider solely from a headline rate. [E08]

## 10. Prioritized implementation proposal — subject to approval

These are planning ranges in engineer-days, not fixed quotations. They exclude waiting for credentials, payment-provider approval and staff availability. Re-estimate after staging inspection; the phases may share work.

| Phase | Proposed scope | Expected benefit | Trade-off / risk | Rough effort | Exit evidence |
|---|---|---|---|---|---|
| 1. Close public and financial defects | F01, F02, F04; deny wrong credentials; correct cloud allocation; fail explicitly on pricing errors; targeted negative tests | Immediate reduction in unauthorized access and incorrect billing | Auth changes may require session renewal; allocations require migration policy | 3–6 days | Staging wrong-credential rejection; exact paise totals; no zero-price fallback |
| 2. Make failures recoverable | Durable payment inbox/ledger/outbox; F03, F05–F08; print action checkpoints; secure file references; sync ownership | Paid orders survive retries/crashes without double work | Highest integration risk; requires fault injection and Windows acceptance | 8–15 days | Crash/replay tests; no duplicate sub-jobs; restart recovery; safe download tests |
| 3. Prove operations and releases | Activate schema gate; role/store checks; backups/restore; capability routing; finish acceptance phases; canary release to one PC | Trustworthy operation across shops | Requires store participation and some paper; hardware behavior may vary | 5–10 days plus onsite sessions | Signed acceptance matrix; verified running versions; restore result; live schema evidence |
| 4. Reduce routine staff work | Unified exceptions/production queue; three lanes; finishing manifest; QR handover; deadline/capacity estimates | Lower handling time and missed handoffs | Requires staff feedback; avoid hiding essential details | 5–10 days | Measured baseline comparison, on-time completion, staff task trials |
| 5. Simplify and expand selectively | Consolidate large modules, authentication adapters and current docs; benchmark managed transport; pursue proven profitable features | Lower maintenance burden and safer growth | Refactor can reintroduce old driver defects if too broad | Estimate after phases 1–4 | Stable workflows and measured benefit before each expansion |

Dependencies: individual authorization precedes safe tenant expansion; ledger correctness precedes payment rollout; attempt tracking precedes aggressive failover; preflight precedes unattended printing; reliable time/cost data precedes automated routing optimization.

### Required regression scenarios

- Cloud legacy login rejects incorrect, empty and misconfigured credentials.
- A ₹100 batch across three jobs totals exactly ₹100 after allocation, including rounding.
- Duplicate, delayed and concurrent payment events neither add money twice nor regress production status.
- A crash after inbox receipt but before payment commit is recoverable.
- A partial mixed-colour print does not repeat completed sections automatically.
- A timeout after spooling produces an explicit uncertain-output decision.
- Internet loss preserves local counter records and reconciles without overwriting cloud payments.
- A malformed file, unsupported type, excessive upload, private-network URL or quote failure is held/rejected clearly.
- OSP Konica duplex then simplex, 2-up/4-up, colour split and scaling remain correct on paper.
- The operator's actual submitted service quantities match pricing inputs.
- Two PCs cannot independently release the same attempt; expired claims cannot authorize stale workers.
- A missing database check produces “unverified”, not release-ready green.

## 11. Development and operating governance

Use one named technical owner for releases even if multiple AI coding tools contribute. Each task should begin by fetching the repository and reading current rules, memory and acceptance status. Record the starting commit, touched contracts, verification, open risks and deployed PC versions. Do not mix unrelated experiments into a printing reliability release.

Keep a protected, reviewed path into `main` and promote one approved release to a canary PC before the other machines. Verify current repository protection settings before claiming they exist. Every schema change must remain compatible with the last supported agent release during rollout. The repository's boot-time update scripts help distribution but do not replace compatibility and rollback decisions.

Preserve the locked Konica queue selector, its call sites, logging placement and two-page simplex/duplex proof design. Any later request to change those should follow the project's special confirmation rule. This review required no such modification.

Update architecture/security/backlog documents to distinguish historical fixes, implemented code, deployed state and accepted behavior. The current architecture document includes older descriptions of credentials, retired modules and webhook secrets; treat code and current verified deployment evidence as authoritative, not every historical paragraph.

Use AI for document assistance, triage summaries and development support where useful. Keep pricing, authorization, payment reconciliation and release eligibility deterministic. Human review remains appropriate for document fidelity, ambiguous output and high-cost reprints.

## 12. Verification record and limits

### Local synthetic checks

| Check | Observed result |
|---|---|
| Wrong legacy password with configured non-matching hash | HTTP 200 / `ok: true`; mock JWT returned |
| ₹100 cloud batch payment over two jobs | ₹100 assigned to each job; summed ₹200 |
| Pricing function raises | Order handler returns success and persists quote `0.0` |
| Sync collector reads missing jobs table | Logs error and returns empty list |

These checks executed extracted current function bodies with external dependencies replaced. They are focused reproductions, not integration tests and not proof of live exploitation.

### Repository and CI evidence

- Repository snapshot: `3df7b7e9131bad896b3fa6c2399a89c83f5deb67`.
- GitHub `test` run `34397656956`: successful.
- GitHub production smoke run `34429414056`: successful. This status does not certify every financial/security path.
- GitHub schema run `34397657051`: successful workflow, but actual schema-check step skipped.
- Standard test workflow excludes the two browser suites. The sample autoprint “e2e” test mocks the printer submission; it does not prove physical output. [C06], [C21], [C24]
- A selected local test batch made partial progress but stalled and was interrupted; no complete local suite pass is claimed. Local dependency installation was separate from the repository and not an exact production dependency reproduction.

### Not verified

Current live RLS/grants and storage privacy; whether all migrations ran; live gateway activation and webhook configuration; production authentication secrets; current Windows agent commits; actual Epson/Konica output today; printer firmware and Windows security posture; implemented backup schedules and restoration; business volume, margins and staff throughput. No actual losses or exposure counts are asserted.

Older owner-action notes are useful leads, not proof that a dashboard is still misconfigured. Likewise, a September 4 pending test is not proof that it remained pending on September 10; its completion needs evidence.

## 13. Decisions to make after reviewing this proposal

1. Authorize a narrowly scoped first change set covering legacy authentication, cloud payment allocation and pricing failure handling.
2. Choose who owns daily exception closure and cash/payment reconciliation.
3. Confirm the current payment launch plan and the intended role of each shop.
4. Supply two weeks of order counts, job mix, handling time, reprints, machine costs and operating hours for quantitative optimization.
5. Agree on the standard automation lane and the cases that must always receive staff review.

The preferred sequence is **secure entry → correct money → recoverable printing → verified shop operation → workload reduction → selective expansion**.

## References

Code links are pinned to the reviewed commit. Function names and locations in findings identify the relevant evidence.

[C01]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/CLAUDE.md
[C02]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/rate_card.py
[C03]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/docs/ARCHITECTURE.md
[C04]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/device_lease.py
[C05]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/docs/plans/2026-09-04-acceptance-run-state.md
[C06]: https://github.com/deepakbanarjee/printosky/actions/runs/34397656956
[C07]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/api/migrations/SCHEMA_v29_processed_webhooks_rls.sql
[C08]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/ops_watchdog.py
[C09]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/api/inngest.py
[C10]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/api/index.py#L2084-L2135
[C11]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/api/index.py#L1725-L1760
[C12]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/tests/test_webhook_money_fixes.py
[C13]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/api/index.py#L3750-L3765
[C14]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/api/handlers_order.py#L249-L340
[C15]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/store_puller.py#L510-L565
[C16]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/print_server.py#L814-L839
[C17]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/supabase_sync.py
[C18]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/store_puller.py#L367-L377
[C19]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/db_cloud.py#L2596-L2613
[C20]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/api/index.py#L2010-L2083
[C21]: https://github.com/deepakbanarjee/printosky/actions/runs/34397657051
[C22]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/db_cloud.py#L203-L219
[C23]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/tests/test_print_retry_pacing.py
[C24]: https://github.com/deepakbanarjee/printosky/blob/3df7b7e9131bad896b3fa6c2399a89c83f5deb67/tests/test_autoprint_e2e.py
[E01]: https://www.printnode.com/en/docs/api/curl
[E02]: https://www.papercut.com/help/manuals/pocket-hive/how-it-works/
[E03]: https://dashboard.gelato.com/docs/
[E04]: https://dashboard.gelato.com/docs/orders/order_details/
[E05]: https://www.printavo.com/features/
[E06]: https://printo.in/
[E07]: https://supabase.com/docs/guides/database/postgres/row-level-security
[E08]: https://www.cashfree.com/docs/payments/online/webhooks/webhook-indempotency
