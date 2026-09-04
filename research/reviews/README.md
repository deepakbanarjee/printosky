# Review pattern study — print shops, Thrissur & Ernakulam

Reading competitors' Google reviews to find the repeatable failure patterns in
this category, and which of them Printosky can remove.

- `SHOPLIST.md` — which listings to capture, and why each is in the frame
- `CODEBOOK.md` — the themes every review is coded against
- `code_reviews.py` — parses a pasted reviews pane, codes it, compares shops
- `corpus/` — one raw dump per shop, `<city>-<shop>-<locality>.txt`
- `findings/` — the written read for each shop, plus the cross-shop pattern

## Capture: how to get a shop's reviews

There is no automated path from this repo. Google Maps, JustDial and the Kerala
directories are all blocked by this environment's network policy, and Google's
Places API returns only five reviews per place — not enough to see a pattern.
So capture is manual, and it takes about five minutes a shop:

1. Open the listing on Google Maps and click into **all reviews**.
2. Set sort to **Most relevant** — the same sort for every shop, or the
   cross-shop table is comparing different things.
3. Scroll to the bottom so every review is loaded, and click **More** on every
   truncated one. Skipping this systematically under-samples long reviews, which
   are disproportionately the negative ones.
4. Select the pane, copy, and paste into `corpus/<city>-<shop>-<locality>.txt`.
5. Put a comment header on the file: shop, address, date captured, sort order,
   and the star rating and total review count off the listing (the paste does
   **not** carry star values — that has to be recorded by hand).

If you'd rather automate it later, the honest options are a paid scraping API
(Outscraper, SerpAPI and similar return full review sets for a Place ID) run from
a machine with open network access, dumping the same text files into `corpus/`.
The coding side below doesn't care where the text came from.

## Analyse

```bash
cd research/reviews

# one shop
python3 code_reviews.py corpus/thrissur-kundham-college-road.txt

# every shop, side by side — this is the pattern table
python3 code_reviews.py --compare corpus/*.txt

# coded records for deeper slicing
python3 code_reviews.py --json corpus/*.txt > findings/coded.json
```

Output is percent of *substantive* reviews carrying each theme. One-word reviews
("Good", "Nice", "Super") are counted separately and excluded from the
percentages — in this category they're 20–50% of the pane and they'd otherwise
flatten every difference between shops.

## Reading the output honestly

- **Percentages are of reviews, not of customers.** People who complain write;
  people who are served well mostly don't. A 20% rudeness rate does not mean one
  visit in five goes badly — it means rudeness is what one review in five is
  *about*, which is the useful thing.
- **Ratings aren't in the corpus.** Polarity here is inferred from wording.
  Record the listing's star distribution by hand in `SHOPLIST.md` when it matters.
- **Recency beats volume.** A theme concentrated in the last 12 months is a live
  operational problem; the same theme spread over 7 years is category background.
  `neg_share_last_12mo` in the report is the number to watch.
- **Google's paste is imperfect.** Reviewer follow-ups sometimes land inside the
  owner-reply block, and reaction rows and photo captions interleave. The parser
  drops what it can recognise; spot-check any record that looks strange before
  quoting it.
- **The pattern is the point, not the shop.** A single damning review is an
  anecdote. The same complaint at five shops in two cities is a category-level
  gap — and a category-level gap is something a product can close.
