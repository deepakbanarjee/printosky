# Review Codebook — print shops, Thrissur & Ernakulam

Every review in every shop's corpus is coded against the same themes so shops
are comparable. Codes were derived inductively from the first corpus
(Kundham / The Spear, College Road Thrissur, n≈400) and are meant to be stable
across shops — add a code only when a review genuinely doesn't fit, and when you
do, re-run every corpus so the columns stay comparable.

A review can carry several codes. Codes are matched on the review text only,
never on the owner reply (owner replies get their own `M*` codes).

## Positive drivers — why people choose a shop

| Code | Theme | What it captures |
|---|---|---|
| `P_PRICE` | Cheapest / affordable | rate, cheap, low price, affordable, reasonable, budget, economical |
| `P_SPEED` | Fast turnaround | fast, quick, prompt, within minutes, no waiting, on time |
| `P_BULK` | Bulk capacity & slabs | bulk, large quantity, 1000 pages, discount above N copies |
| `P_STAFF` | Good staff behaviour | friendly, helpful, polite, patient, supportive, good dealing |
| `P_QUALITY` | Output quality | clear, neat, good print, good binding, quality paper |
| `P_REMOTE` | Order without visiting | WhatsApp, email, pendrive, online order, prepay, UPI |
| `P_RANGE` | Breadth of services | spiral/hard binding, lamination, DTP, project, thesis, scanning, plotter |
| `P_STUDENT` | Student positioning | students, college, concession, study material |

## Pain points — why people complain

| Code | Theme | What it captures |
|---|---|---|
| `N_STAFF` | Rude / dismissive staff | rude, bad behaviour, arrogant, attitude, ignored, had to beg |
| `N_WAIT` | Wait time & crowding | waited hours, crowded, rush, queue, delay, come back tomorrow |
| `N_STATUS` | No job status / handover | had to ask every time, follow up, no update, no reminder, forgot my job |
| `N_BILLING` | Price disputes & surprises | overcharged, extra charge, hidden charge, made me pay for their error |
| `N_QUALITY` | Bad output | unclear, blurred, lines on page, poor paper, shoddy binding |
| `N_ERROR` | Job executed wrong | duplicates, wrong file, missing pages, wrong side, had to reprint |
| `N_SKILL` | Untrained / churning staff | new staff every time, don't know how, incompetent, slow to learn |
| `N_LOCATION` | Branch / brand confusion | wrong shop, duplicate shops, which branch, hard to find |
| `N_CONTACT` | Can't reach or enquire | phone number please, no answer, no phone enquiry, no online |
| `N_UPTIME` | Shop/infra unavailable | closed, power cut, network down, machine not working, server down |

## Owner-reply behaviour — coded on the reply, not the review

| Code | Theme |
|---|---|
| `M_REPLIED` | Owner replied at all |
| `M_DEFLECT` | Reply blames wrong shop / duplicate shops |
| `M_BOILERPLATE` | Generic "thank you for the feedback" / "thank you for your kind words" |
| `M_MISMATCH` | Thanks-for-kind-words style reply posted on a clearly negative review |
| `M_APOLOGY` | Specific, non-deflecting apology or remedy |

## Signal quality

| Code | Theme |
|---|---|
| `LOW_CONTENT` | Three words or fewer ("Good", "Nice", "Super") — counted, but excluded from theme percentages |
| `TRUNCATED` | Google collapsed the text with "…More" — the full text was not captured |

## Language note

Reviews mix English, Manglish and Malayalam. The matcher includes common
transliterations (`pwoli`, `kollam`, `mosham`, `moodesh`, `nannayi`) — extend
`LEXICON` in `code_reviews.py` as new ones show up rather than hand-coding.

## Known limitations

- **No star ratings.** Copy-pasting from Google Maps does not carry the star
  value, so polarity here is inferred from wording, not measured. Where the star
  distribution matters, read it off the listing by hand into `SHOPLIST.md`.
- **Google's sort order is not a sample.** "Most relevant" over-weights long and
  recent reviews. Capture with the same sort for every shop (see README) or the
  cross-shop comparison is not like-for-like.
- **Truncation.** Long reviews collapse behind "…More"; expand them before
  copying, or accept that the longest (usually most negative) reviews are
  under-coded.
