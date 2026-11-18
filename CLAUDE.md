# CLAUDE.md

Personal price-optimisation assistant for Dutch online shopping. Streamlit app with a Dutch UI; all logic is in `app.py` (about 1,500 lines). It is the owner's personal tool, not a product for others.

## Goal (still being defined)

Paste a product URL, get the cheapest effective way to buy it in the Netherlands: total price including shipping, running promotions (1+1, 2e halve prijs, folder deals) and price per unit.

Direction under discussion, not final until the owner confirms: anchor on the product's EAN (bol and Amazon product pages expose it), look it up on price-comparison sites (a Beslist lookup by EAN found the best price in testing), check AH, Etos, Kruidvat and Jumbo for promos, and keep a watchlist for recurring purchases because promos are time-limited. The goal and research documents live in the claude.ai Project "ecommerce_assitant", not in this repo.

## Commands

```bash
pip install -r requirements.txt pytest ruff
streamlit run app.py
pytest -q
ruff check --select E9,F63,F7,F82 .
```

CI runs the last two. Run them before opening a PR.

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

## Known issues (assessment of 2026-11-17)

- The `google_product` deep scan is dead (Google shut the service down; SerpApi points to the Immersive Product API). Not migrated.
- `google_shopping` results may lack a direct merchant link, so the page-level promo check probably rarely runs. Needs one raw SerpApi response to confirm.
- Shipping is mostly guessed. The volume parser has no grams/ounces and misses "1,5 L x 6" and "duopack".
- The bol API integration has not been tested against the live API.
