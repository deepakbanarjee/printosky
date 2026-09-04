# The category pattern — working hypotheses

Written after one shop (Kundham, Thrissur). These are **predictions**, stated so
that the next captures can falsify them rather than just accumulate more reading.
Update each line with ✅ confirmed / ❌ refuted / ~ mixed as shops land, and note
which shop moved it.

| # | Hypothesis | Status | Evidence |
|---|---|---|---|
| H1 | Price is the only stated driver at scale; every other positive is secondary | — | Kundham: price is the modal theme |
| H2 | The top complaint is **staff behaviour**, not print quality — the category fails on service, not on machines | — | Kundham: rudeness ≫ quality complaints |
| H3 | Wait time is universally tolerated *when expected* and resented when unannounced — complaints are about surprise, not duration | — | |
| H4 | A distinct **"finished but not handed over"** complaint exists at every busy shop: the job is done and the customer must still chase it | — | Kundham: several |
| H5 | Billing disputes are mostly about **paying for the shop's error**, not about the rate card | — | Kundham: duplicates, unwanted pages |
| H6 | Customers cannot get a price without travelling, and say so | — | Kundham: explicit no-phone-enquiry policy |
| H7 | Same-name / same-street shop confusion is endemic in Thrissur; unclear whether it exists in Ernakulam | — | Kundham: 40+ deflecting replies |
| H8 | Shops with **higher ratings** differ mainly on staff warmth and communication, not on price or speed | — | untested — needs a high-rated shop captured |
| H9 | Negative themes are **shifting toward behaviour over time**; older complaints skew to wait/quality | — | Kundham: recent negatives are behaviour-led |
| H10 | Remote ordering (WhatsApp/email/pendrive) is praised wherever it exists and is not yet table stakes | — | |

## What would make this a real read

- **≥6 shops**, across both cities, including at least two well-rated ones (H8 is
  the hypothesis that pays, and it can only be tested on shops people like).
- **Same sort order** on every capture, or the table isn't comparable.
- Then: `python3 code_reviews.py --compare corpus/*.txt`, and read down the
  columns. A theme that is high everywhere is the category's shape. A theme that
  is high at one shop is that shop's problem. A theme that is low *only* at the
  well-rated shops is the one worth copying.

## Where this feeds Printosky

Each confirmed hypothesis should land as a line in `SPRINT_BACKLOG.md` or be
struck out. The current candidates, all of which are systems gaps rather than
equipment gaps:

- quote before travel (answers H6)
- "ready for pickup" push (answers H4, and much of H3)
- immutable order record: files, page count, amount (answers H5)
- stated reprint-on-us rule for shop errors (answers H5)
- one canonical listing and a no-deflection review-reply policy (answers H7)
