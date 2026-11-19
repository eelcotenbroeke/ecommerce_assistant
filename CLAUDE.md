# CLAUDE.md

Personal price-optimisation assistant for Dutch online shopping. Streamlit app with a Dutch UI; all logic is in `app.py` (about 1,500 lines). It is the owner's personal tool, not a product for others.

## Goal (still being defined)

Paste a product URL, get the cheapest effective way to buy it in the Netherlands: total price including shipping, running promotions (1+1, 2e halve prijs, folder deals) and price per unit.

Direction under discussion, not final until the owner confirms: anchor on the product's EAN (bol and Amazon product pages expose it), look it up on price-comparison sites (a Beslist lookup by EAN found the best price in testing), check AH, Etos, Kruidvat and Jumbo for promos, and keep a watchlist for recurring purchases because promos are time-limited. Update 2026-11-18: from the Streamlit Community Cloud server, Beslist answers with HTTP 202 and a small page (the signature of an AWS WAF bot check) instead of the product page, so the Beslist route does not work from the deployed app. A bot check is a block: do not work around it (no browser emulation, no changed User-Agent, no proxies). Chosen alternative (owner, 2026-11-18): Google Shopping searched by EAN through SerpApi (`serp.py`, key in Secrets). First real answers seen 2026-11-19 (owner's raw-answer download, fixture `tests/data/serp_real_3474637283414.json`): Google Shopping does not match a barcode exactly. For EAN 3474637283414 it returned 40 loosely related shampoos and the right product (Kérastase Gloss Absolu, Celini.nl) was number 10; there is no barcode or GTIN in the answer. So the app never guesses the product: `serp.py` groups the results into candidate products, an optional *Productnaam* field ranks them (and settles the choice only on a clear match), and otherwise the screen shows a list and the owner taps his product; only then are the shops fetched (second search). The bare EAN is also unreliable as query: for EAN 4013594586856 Google Shopping returned nothing at all (`shopping_results_state: "Fully empty"`, fixture `tests/data/serp_real_4013594586856_empty.json`). So when the owner fills in *Productnaam*, Google is asked for that name (not the EAN); the screen shows the search term, and an empty answer for a bare EAN tells him to fill in the name. Staggered lookup (owner agreed 2026-11-19, built slice by slice): identify the product by EAN/GTIN first; if that gives nothing, by brand + MPN (manufacturer's article code, read from the shop's page; a shop's own SKU such as Zalando's or Wehkamp's only identifies the product at that shop); last by product name. Each rung that asks Google costs a SerpApi search, so the next rung is chosen from what is known about the product, not by trying all blindly. Slice A (built): `pageprobe.py` + `probe_page.py`, the expander "Test winkelpagina's" fetches pasted product links once (honest User-Agent, no SerpApi searches), reports per shop whether the page can be read (a refusal or bot check is a block: reported, never worked around) and which EAN, MPN, brand, name and SKU it holds, and offers a trimmed JSON download to build fixtures from. Next: slice B (parser per shop from real pages), slice C (the ladder itself). First probe results (owner, 2026-11-19, fixture `tests/data/probe_real_coolblue_dyson.json`): Coolblue is readable (JSON-LD has brand, name, the shop's own sku and an offer with price and availability; the manufacturer's code stands in a spec row "Fabrikantcode", which the parser now reads as MPN; no EAN was found in JSON-LD, microdata or spec rows, so the probe also lists barcodes found next to an "ean"/"gtin" label anywhere in the page code, as hints only). Zalando and Wehkamp answered HTTP 403: blocked from the cloud app, reported and not worked around; for such shops the name can still come from the pasted link itself. The goal and research documents live in the claude.ai Project "ecommerce_assitant", not in this repo.

Confirmed by the owner (2026-11-18): he buys online. Delivery from bol and Amazon is free for him, so those shops have effective shipping 0. He trusts bol and Amazon most: when one of them is less than 10% above the cheapest total, recommend it instead of the cheapest. This is implemented in `choice.py`.

## Commands

```bash
pip install -r requirements.txt pytest ruff
streamlit run app.py
pytest -q
ruff check --select E9,F63,F7,F82 .
```

CI runs the last two. Run them before opening a PR.

`python probe_beslist.py [EAN]` is a live check (two requests to Beslist, honest User-Agent): run it on the machine that will host the app to see whether Beslist can be read without a browser. It is not part of CI and the Claude sandbox cannot reach Beslist.

## Secrets

Read through `st.secrets`: `SERPAPI_KEY`, `BOL_CLIENT_ID`, `BOL_CLIENT_SECRET`. Optional: `SERPAPI_MONTHLY_LIMIT` (searches per month the phone screen may use; default 250, the owner's maximum: 100 on 2026-11-18, raised to 250 the same evening). Locally in `.streamlit/secrets.toml` (git-ignored). Never commit keys or paste them into PRs, issues or logs.

## Workflow

- Never push to `main`. Work on a branch named `claude/<topic>` and open a pull request.
- The owner has authorised Claude to merge its own pull requests (squash) once `ci` is green; no further approval is needed (instruction of 2026-11-18). Use auto-merge when the `main` ruleset allows it, otherwise merge through the REST API after checking that `ci` passed on the PR's latest commit. Never merge with `ci` failing or missing, and only merge PRs Claude opened. Branches are deleted after merge.
- Tooling: `gh pr create/view/merge` do not work in Claude sessions (GraphQL is blocked). Use the REST API through `gh api repos/{owner}/{repo}/pulls...`. Auto-merge is armed with `PUT /repos/{owner}/{repo}/pulls/{n}/ccr/auto_merge`. Deleting remote branches is blocked, so rely on delete-branch-on-merge.
- Keep PRs small and single-purpose. In the description say what you tested and what you could not test.
- The required status check is `ci`. Do not weaken or skip checks to get green.
- Do not add dependencies without explaining why in the PR.
- Prices from shops are snapshots. Never present a price or promo as current unless it was read from the live page in this session, and say when it was read.

## Code conventions

- User-facing strings are Dutch. Identifiers and comments mix Dutch and English: follow the surrounding style.
- New logic goes into small pure modules next to `app.py` (for example `ean.py`), each with tests in `tests/`; `app.py` only wires them in. No network or Streamlit calls inside the pure modules.
- Keep pure helpers (`parse_price`, `extract_volume_ml`, ...) free of Streamlit calls so they stay testable. `import app` works in tests because Streamlit runs in bare mode.
- Prices are floats in EUR, formatted with `fmt_eur`.
- `app.py` has CRLF line endings. Edit it without converting them and check `git diff --stat` before committing: a whole-file diff means the line endings were lost.
- The owner uses the app mainly on his phone. `app.py` opens on the EAN screen (`ean_page.py`, texts and rows from `lookup_view.py`, chain in `lookup.py`); the old search sits behind the "Zoeken (oud)" toggle. The offers are a list of blocks (shop, total, price/shipping/delivery, the shop's own title and details, a link to the shop; the recommended one first), not a table. The input and the search button are one `st.form` (Enter on the phone keyboard searches, and the text is read only on submit); each result says which EAN was searched and which product the source found. When Google returns several products and the optional *Productnaam* field does not settle it, the screen shows a list of candidate products (`Choose` result in `lookup.py`, `serp.candidates/rank/auto_pick`) and the shops are fetched only after the owner taps one (`lookup.finish_choice`); "Ander product kiezen" goes back to the list without a new search. `ean_page.render()` returns at once without a Streamlit session, because a form created during `import app` in the tests leaks into the next AppTest ("Forms cannot be nested"). `?url=<bol link or EAN>` runs the lookup straight away. All fetching goes through `fetching.py` (honest User-Agent); if a shop blocks it, report that and stop, never work around it.

## Known issues (assessment of 2026-11-17)

- SerpApi route (verified against real answers 2026-11-19): a lookup is 1 search for the candidate list plus 1 for the chosen product's shops (2 when the product name or a single candidate settles the choice at once). The owner accepts at most 250 searches a month (the counter is account-wide, old search and tests included): the screen reads the month's usage from SerpApi's free Account API (`budget.py`), checks the budget before each step, caches worked lookups and candidate lists for 15 minutes per session and counts calls. The store list (`google_immersive_product`, `more_stores=true`) is rich: link (some are relative `/goto?url=...`, resolved to `https://www.google.com/goto?...`), title (often with a promo prefix such as "2 voor 10.00:", "Bonus 2e gratis:", "1+1 gratis:", "2+3 gratis:", "47% korting |", which becomes "Actie: ..." in the details), rating, `tag`, `details_and_offers`, price and shipping. Shipping is missing for some shops (AH, Kruidvat, Trekpleister, Leef in the fixture): shown as "onbekend", never guessed. Not verified: whether the `/goto` links open the shop, and how well a search on the product name works in practice (built 2026-11-19, tested only with fakes).
- Promos are only shown as text ("Actie: ..."); they are not in the effective total or the price per unit yet.
- The `google_product` deep scan is dead (Google shut the service down; SerpApi points to the Immersive Product API). Not migrated.
- `google_shopping` results have no direct merchant link (confirmed 2026-11-19: only `product_link` to Google), so the page-level promo check cannot run from them; the store list has the links.
- Shipping is mostly guessed. The volume parser has no grams/ounces and misses "1,5 L x 6" and "duopack".
- The bol API integration has not been tested against the live API.
