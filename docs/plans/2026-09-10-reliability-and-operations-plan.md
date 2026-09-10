# Printosky reliability and operations implementation plan

**Date:** 10 September 2026  
**Owner:** Deepak Banarjee  
**Technical lead:** To be assigned  
**Status:** Proposed; application changes await owner approval  
**Evidence baseline:** `3df7b7e9131bad896b3fa6c2399a89c83f5deb67`  
**Related assessment:** [Professional review](../reviews/2026-09-10-professional-review.md)

## 1. Authorization and handoff — read first

The owner authorized publishing the review and creating this detailed plan. That authorization does **not** approve fixing code, modifying production data, activating payment gateways, running real transactions, sending messages, changing printer behavior or deploying implementation changes.

At the start of every implementation task:

1. Fetch GitHub and inspect the working tree; preserve unrelated work.
2. Read `.agents/AGENTS.md`, `CLAUDE.md`, this plan, the review, and the latest relevant acceptance notes.
3. Check whether the selected task has explicit owner approval. Record its scope and starting commit.
4. Reproduce the finding on current code. The review is pinned historical evidence, not proof that later commits still contain the defect.
5. Identify the actual active cloud/local entry points. Tests for a retired implementation do not close a finding in another handler.
6. Record changes, evidence, limitations, release status and next action in this plan's execution ledger. Keep the original review unchanged as the baseline.

### Hard rules inherited from the repository and this task

- Application code remains untouched until approval for the proposed implementation scope.
- Fail loud: required work that fails must produce an actionable human-visible signal. Missing evidence cannot be green.
- Preserve ordinary printing when new settings are absent; compare PDF geometry and output before and after.
- Preserve the locked Konica queue selector and both call sites, early logging configuration, and two-page proof design. Follow `CLAUDE.md`'s special double-confirmation rule if a future change actually touches them.
- Keep customer-facing communications branded Printosky.
- Never commit secrets, real payment proofs, customer files or production database exports.
- No force pushes or destructive data repairs as part of this plan. Corrections to financial history require an independently reviewed reconciliation proposal.
- Never claim deployment from a merge alone: store PCs run their own installed versions.
- No automatically repeated printing after an uncertain physical side effect.

## 2. Outcome and completion standard

The outcome is an order-to-collection process that is secure, financially consistent, recoverable after outages, and easier for counter/production staff.

Completion requires all of the following:

| Outcome | Required evidence |
|---|---|
| Secure entry | Wrong credentials fail across active auth routes; staff roles/store access are tested. |
| Correct money | Payment ledger and allocations agree exactly in paise; duplicate/concurrent events do not change totals incorrectly. |
| Recoverable printing | Completed actions do not automatically repeat; crash/timeout ambiguity is visible and requires a decision. |
| Reliable synchronization | Read failures are unhealthy; local events cannot overwrite cloud-owned payment state. |
| Verified releases | Required checks actually execute; each production PC has a known accepted version. |
| Lower workload | Two-week baseline compared with pilot handling time, reprints and completion times. |

No numeric savings forecast is approved. Targets below are provisional until actual workload is measured.

## 3. Work packages and sequence

Planning estimates are engineer-days, excluding external access, approval waits and onsite availability. Roles are proposed responsibilities, not commitments by named staff.

| Package | Priority | Review findings | Dependencies | Accountable role | Estimate |
|---|---|---|---|---|---|
| WP00 Baseline and staging | P0 | All | Implementation approval | Technical lead | 1–2 days |
| WP01 Authentication rejection | P0 | F01 | WP00 | Backend engineer | 1–2 days |
| WP02 Batch allocation and quote failures | P0 | F02, F04 | WP00 | Backend engineer + owner for commercial rule | 2–4 days |
| WP03 Durable payments | P0 | F03 | WP02 payment contract | Backend/database engineer | 4–7 days |
| WP04 Print attempt recovery | P1 | F05, F06 | WP00; coordinate WP03 states | Store-agent engineer | 4–7 days |
| WP05 Synchronization ownership | P1 | F07 | WP03/WP04 contracts | Backend/store-agent engineer | 3–5 days |
| WP06 File access and staff scope | P1 | F08, F09 | WP01; storage inventory | Backend engineer | 3–5 days |
| WP07 Release, schema and recovery gates | P1 | F10 | WP00; completes after WP03–06 | Technical lead | 2–4 days |
| WP08 Capability routing | P1 before expansion | F11 | WP04/WP05, verified store capabilities | Backend engineer + production lead | 2–3 days |
| WP09 Staff workflow pilot | P1 | Operational recommendations | Stable WP01–07 | Frontend engineer + shift lead | 5–8 days |
| WP10 Maintainability and documentation | P2 | F12 | Stable contracts | Technical lead | 3–5 days initial tranche |

Some packages overlap. Do not add these estimates to the review's phase estimates; these are a more granular breakdown. A single maintainer should expect roughly 30–50 working days plus a two-week measurement window, subject to refinement after WP00. A small team can overlap independent work only after agreeing shared contracts and release ownership. This plan does not itself authorize agent delegation.

### WP00 — Establish a trustworthy baseline

**Steps**

1. Confirm current SHA, active website/API routes, payment launch status, stores and devices.
2. Inventory production dependencies without exposing secret values: schema versions, permissions, storage bucket visibility, gateway endpoint, alert destinations and backup configuration.
3. Recreate an isolated staging environment with synthetic accounts, private test storage, sandbox payment events and no real printer/message destinations.
4. Install declared dependency versions; record Python, operating system and relevant printer-driver versions. Do not silently treat a newer local SDK as the production baseline.
5. Repeat the focused reproductions; obtain a complete bounded test result or record the exact stalled/failing test.
6. Capture representative ordinary print outputs and the known-good Konica queue configuration.

**Deliverables:** environment inventory, route-to-handler map, baseline verification log, approved task scope.

**Exit:** every unknown is either resolved or has an explicit test/owner; no unverified check is counted as passed.

### WP01 — Close the legacy authentication bypass

**Likely touch points:** `api/index.py`, `netlify/functions/auth.js`, active browser login callers, focused auth tests.

1. Enumerate every login entry and its intended credential type.
2. Add a negative behavioral test for an incorrect password with a configured hash, plus empty configuration and inactive staff.
3. Remove success fall-through; only a verified credential may mint a session.
4. Ensure errors return a complete response and fail closed.
5. Align response contracts across Netlify/Python without changing unrelated staff workflows.
6. Verify off-LAN cloud login and on-LAN counter login in staging.

**Acceptance:** incorrect credentials never produce `ok: true` or a JWT; valid authorized login works; unavailable auth dependencies return explicit failure.

**Rollback:** keep the unsafe endpoint disabled if the new flow has trouble; do not restore an authentication bypass to regain convenience. Document a controlled staff access alternative before release.

### WP02 — Correct batch allocation and pricing failure handling

**Likely touch points:** `_process_razorpay_payment`, shared payment calculations, `db_cloud.py`, `api/handlers_order.py`, cloud payment and order tests.

1. Agree allocation rule: allocate by accepted line values, with deterministic handling of discounts, delivery and indivisible paise. Zero-value lines must not produce divide-by-zero behavior.
2. Implement one pure allocation routine used by active handlers; do not patch only the deprecated receiver.
3. Store provider payment identity once; temporary per-job allocations must sum exactly while WP03 establishes the full ledger.
4. Make quote exceptions return pricing unavailable or an explicitly unpriced staff-review draft.
5. Validate copies, page ranges and colour selection against trusted document metadata; document unsupported settings.
6. Add tests for ₹100 over two and three jobs, discounts, duplicate delivery, malformed specifications and pricing exceptions.

**Acceptance:** exact totals; no free-price fallback; no invalid quote moves into production.

**Historical data:** produce a read-only discrepancy list only if later authorized. Do not rewrite old revenue totals automatically.

### WP03 — Make payment processing durable

**Design before editing:** approved schema for payment inbox, payments, allocations, outgoing events and processing states; unique keys and transaction boundaries.

1. Verify signature and payload bounds before recording an event.
2. Durably accept the event into an inbox before acknowledging it. If persistence fails, return a retryable failure.
3. In a transaction, recognize provider/payment identity, validate order, currency and amount, write the payment/allocations and update balances.
4. Separate payment status from production status; late events cannot move a ready/delivered order back to Paid.
5. Commit notification tasks with the business update, then deliver asynchronously with retries.
6. Handle concurrent deposits/balances without read-modify-write loss.
7. Add provider reconciliation, unmatched-payment review and refund/reversal records.
8. If Cashfree is selected later, implement a provider adapter against this same contract. Gateway selection is not part of document-publishing approval.

**Fault tests:** crash before inbox commit; after inbox but before ledger commit; after commit but before notification; duplicate events with different event IDs; concurrent distinct payments; delayed events after collection.

**Acceptance:** replay converges to one correct financial outcome; outgoing messages do not determine whether payment is recorded; every unreconciled item is visible.

**Rollout:** shadow calculations and compare totals, then a limited canary. Do not execute live payment tests without specific authorization for real transactions.

### WP04 — Make print attempts safe to recover

**Likely touch points:** `store_puller.py`, `device_lease.py`, `print_server.py`, shared attempt storage and tests. Locked printer areas remain protected.

1. Define order/task/action IDs and file/spec hashes.
2. Persist action states before submission, including owner, attempt token and renewable lease.
3. Record spool evidence after dispatch; distinguish spooled from confirmed physical output.
4. Resume only actions proven not submitted. A timeout after possible submission enters `needs_output_check`.
5. Prevent stale workers acting after ownership changes; test two devices racing and lease expiry during a long task.
6. Retain recoverable files until output/retention policy permits deletion.
7. Provide a staff recovery action: inspected output, resume remaining actions, or authorized reprint with reason.
8. Make missing SumatraPDF or unsupported conversion an explicit hold for jobs whose settings cannot be guaranteed.

**Acceptance:** submit A/B successfully and fail C; restart; A/B do not print again automatically. Repeat with crash before and after spooling, expired claims, disk-full and network loss.

**On-paper gate:** ordinary PDF, duplex then simplex, 2-up/4-up, mixed colour and scaling on the actual intended devices. PDF-only mocks are insufficient evidence.

### WP05 — Replace blind sync with explicit ownership

1. Document field authority: cloud payments, local counter events, agent print events and management annotations.
2. Return typed collection outcomes: successful empty, successful non-empty, failed.
3. Make any required read failure unhealthy; include source and last successful timestamp.
4. Introduce a local outbox/change cursor instead of relying on repeated latest-500 snapshots.
5. Enforce version/ownership checks; surface conflicts rather than applying last-writer-wins to money.
6. Test a historical job outside the latest 500, a stale local Paid/Printed value and duplicate event delivery.
7. Tune fallback polling after measuring query load and latency. Keep bounded backoff and avoid bookkeeping-triggered retry loops.

**Acceptance:** all accepted local events eventually reconcile; cloud money is preserved; failures cannot be displayed as empty healthy data.

### WP06 — Restrict files and establish individual access

1. Replace arbitrary order file URLs with owned storage references.
2. Validate file signatures, sizes, approved types and document preflight limits; constrain conversion resources.
3. If remote fetching remains, validate destination and redirects and reject private/local addresses.
4. Verify bucket access; use private documents/proofs and short-lived authorized URLs.
5. Introduce individual staff identities, store membership, permissions and revocation; remove shared identity assumptions incrementally.
6. Add durable login throttling and verified customer/order access.
7. Plan secure browser sessions and CSP with the separate LAN-agent origin and CSRF needs accounted for.
8. Agree retention with reorder consent; exclude unresolved jobs from indiscriminate cleanup.

**Acceptance:** one customer/store cannot retrieve another's data; arbitrary/private-network URLs and oversized uploads fail; valid counter and customer workflows still work.

### WP07 — Prove releases, schema and backups

1. Make missing schema-check prerequisites explicit and blocking for releases that require schema verification.
2. Separate process liveness, dependency readiness, freshness and business success.
3. Prepare additive, backward-compatible migrations and check them against supported old agents.
4. Verify a backup and restore into an isolated environment; inventory SQLite, cloud rows and storage objects separately.
5. Record recovery point/time objectives after observing the restore; provisional goals require owner agreement.
6. Establish canary release, per-PC version reporting and documented rollback.
7. Complete the applicable September acceptance cases and record dates, devices, results and outstanding exceptions.

**Acceptance:** actual schema check executed; restored data usable; every active PC's version known; green status requires evidence.

### WP08 — Route by real capability and completion time

1. Hydrate routing input from accepted document/specification, including paper, colour, finishing and pickup constraints.
2. Evaluate hours in `Asia/Kolkata` for current stores; explicitly model future store timezones.
3. Verify current printer/finishing capability with the shift lead.
4. Estimate queue demand in machine and finishing minutes; include transfer and collation time.
5. Start in recommendation-only mode and compare with staff choices before enabling automatic assignment.

**Acceptance:** an A3/colour/finishing job cannot be assigned using default B&W/A4 assumptions; pickup promise is preserved; missing capability data creates review, not a false match.

### WP09 — Reduce staff workload with a measured pilot

1. Measure two weeks of intake time, staff touches, paid-to-start latency, completion time, reprints and reconciliation exceptions.
2. Agree Express, Assisted and Scheduled-production eligibility rules with staff.
3. Present one production queue with owner, deadline, current stage, exception reason and next action.
4. Link finishing transfers to the same order with sending/receiving acknowledgement.
5. Add pickup-code/QR handover with balance visibility and authorized completion.
6. Pilot one shift/store; observe representative staff tasks and correct confusion before expansion.
7. Compare with baseline using matching job types and busy/quiet periods.

**Proposed measures:** p95 paid-to-agent acknowledgement below 60 seconds for eligible jobs during open hours; no unexplained daily payment variance; no automatic duplicate output; at least 95% ready by promised time after baseline validation. Handling-time savings must not increase reprints or complaints.

### WP10 — Reduce maintenance risk

1. Extract one payment domain and one authorization contract from parallel implementations.
2. Move shared response utilities out of the main router to reduce circular coupling.
3. Split large browser scripts into reusable modules around real staff tasks.
4. Inventory duplicate/retired paths, confirm no active callers, then propose retirement separately.
5. Refresh architecture, security, deployment and acceptance documentation with clear implemented/deployed/verified labels.
6. Pin reproducible dependencies and assign upgrade ownership.

**Acceptance:** changes are behavior-preserving and independently reviewable. No framework rewrite is required. Printer geometry and known-good driver behavior remain regression gates.

## 4. Proposed release procedure

For each approved work package:

1. Create a focused branch from current `main` and record scope.
2. Add the smallest meaningful regression test for the observed defect.
3. Implement only approved changes; review database and external side effects.
4. Run relevant unit/contract tests and staging scenarios. Record failures and skipped prerequisites honestly.
5. Prepare a reviewable PR with evidence, migration compatibility, operator instructions and rollback steps.
6. Obtain any implementation/deployment authorization still absent from the owner's approved scope.
7. Promote the approved commit; verify cloud version and one canary PC independently.
8. Observe the agreed pilot workload and close exceptions before rolling out to remaining PCs.
9. Update the execution ledger and acceptance evidence.

Because cloud platforms build from `main`, implementation merges must be treated as release actions. A docs-only publication can also trigger configured CI/builds but changes no application behavior.

## 5. Risks, trade-offs and stop conditions

| Decision | Benefit | Cost/risk | Control |
|---|---|---|---|
| Incremental hybrid improvement | Preserves tested printer/business knowledge | Existing coupling remains during transition | Small packages and contract tests |
| Durable ledger/inbox/outbox | Recoverable money and notifications | Schema/transaction complexity | Additive migration, shadow comparison, fault tests |
| Explicit uncertain-output hold | Avoids duplicate paper | Some failures need staff inspection | Clear recovery screen and action-level evidence |
| Shorter reconciliation interval | Faster pickup after realtime failures | More queries and potentially more cost | Measure traffic, bounded polling/backoff |
| Individual identity/private storage | Better accountability and isolation | Session and URL migration work | Staging compatibility and phased rollout |
| Production scheduling | Less missed finishing and deadline confusion | Requires staff adoption and accurate estimates | Pilot with actual tasks and measured feedback |

Stop a release when there is unexplained payment variance, unauthorized access, duplicate print output, changed Konica sides/geometry, unreadable required data reported healthy, or an unexecuted required check. Preserve evidence and return to the last safe operating mode; never erase financial records to make totals look clean.

## 6. Evidence and execution ledger

**Current implementation status:** WP00–WP10 are NOT STARTED and await approval. Research/document preparation is complete. No repair is implied by adding these files.

| Date | Work | Authorization | Evidence/result | Next action |
|---|---|---|---|---|
| 2026-09-10 | Code/architecture/operations review | Analysis only | Review pinned to `3df7b7e`; four local synthetic checks; remote CI metadata inspected; local selected suite interrupted | Publish requested documents |
| 2026-09-10 | Publish review and detailed plan | Owner: “push this to github; create a detailed plan” | Documentation-only change; original report preserved; links and changed-file scope checked before publication | Owner reviews implementation scope |

For future approved tasks, append: task ID, owner, approval reference, baseline SHA, changed paths, regression result, staging result, schema status, deployed versions, physical checks, remaining risks and next action. Keep commits linked from GitHub history rather than inventing a commit ID before publication.

## 7. First recommended approval package

Approve **WP00, WP01 and WP02** as the first bounded implementation scope: staging/baseline verification, rejection of incorrect credentials, correct cloud batch allocation and explicit pricing failures.

This is a recommendation only. Real payments, production data repair, printer-setting changes and expansion routing require their own concrete scope and evidence before execution.
