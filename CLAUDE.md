# CLAUDE.md

Personal price-optimisation assistant for Dutch online shopping. Streamlit app with a Dutch UI; all logic is in `app.py` (about 1,500 lines). It is the owner's personal tool, not a product for others.

## Goal (still being defined)

Paste a product URL, get the cheapest effective way to buy it in the Netherlands: total price including shipping, running promotions (1+1, 2e halve prijs, folder deals) and price per unit.

Direction under discussion, not final until the owner confirms: anchor on the product's EAN (bol and Amazon product pages expose it), look it up on price-comparison sites (a Beslist lookup by EAN found the best price in testing), check AH, Etos, Kruidvat and Jumbo for promos, and keep a watchlist for recurring purchases because promos are time-limited. Update 2026-11-18: from the Streamlit Community Cloud server, Beslist answers with HTTP 202 and a small page (the signature of an AWS WAF bot check) instead of the product page, so the Beslist route does not work from the deployed app. A bot check is a block: do not work around it (no browser emulation, no changed User-Agent, no proxies). Alternatives are being weighed. The goal and research documents live in the claude.ai Project "ecommerce_assitant", not in this repo.

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

Read through `st.secrets`: `SERPAPI_KEY`, `BOL_CLIENT_ID`, `BOL_CLIENT_SECRET`. Locally in `.streamlit/secrets.toml` (git-ignored). Never commit keys or paste them into PRs, issues or logs.

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
- The owner uses the app mainly on his phone. `app.py` opens on the EAN screen (`ean_page.py`, texts and rows from `lookup_view.py`, chain in `lookup.py`); the old search sits behind the "Zoeken (oud)" toggle. `?url=<bol link or EAN>` runs the lookup straight away. All fetching goes through `fetching.py` (honest User-Agent); if a shop blocks it, report that and stop, never work around it.

## Known issues (assessment of 2026-11-17)

- The `google_product` deep scan is dead (Google shut the service down; SerpApi points to the Immersive Product API). Not migrated.
- `google_shopping` results may lack a direct merchant link, so the page-level promo check probably rarely runs. Needs one raw SerpApi response to confirm.
- Shipping is mostly guessed. The volume parser has no grams/ounces and misses "1,5 L x 6" and "duopack".
- The bol API integration has not been tested against the live API.
