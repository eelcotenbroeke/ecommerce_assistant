# ecommerce_assistant

A personal Streamlit app (Dutch interface) that helps find the cheapest *effective* way to buy a product in the Netherlands: it normalises offers to price per liter, including multipacks, sets, shipping and multi-buy promotions such as "1+1 gratis".

Status: early prototype, one file (`app.py`). The direction is being reworked; see "Known issues".

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Also works in a GitHub Codespace (`.devcontainer` starts the app on port 8501).

## Secrets

The app reads these through Streamlit secrets. Locally put them in `.streamlit/secrets.toml` (git-ignored); on Streamlit Community Cloud use the app's secrets settings. Never commit keys.

| Name | Used for | Required |
|---|---|---|
| `SERPAPI_KEY` | Google Shopping search through SerpApi (costs credits per search) | yes |
| `BOL_CLIENT_ID`, `BOL_CLIENT_SECRET` | bol.com catalog API (needs a bol affiliate account) | no |

## Development

```bash
pip install -r requirements.txt pytest ruff
ruff check --select E9,F63,F7,F82 .
pytest -q
```

CI (`.github/workflows/ci.yml`) runs the same two checks on every pull request. Work happens on branches and pull requests; `main` is not pushed to directly. See `CLAUDE.md` for the working agreement.

## Known issues

- The `google_product` deep scan no longer works (Google shut that service down; SerpApi points to its Immersive Product API). Not migrated yet.
- Google Shopping results may lack a direct merchant link, so the promo check on shop pages probably runs for few results. Needs confirming against one raw SerpApi response.
- Shipping costs are mostly estimated. The volume parser does not handle grams/ounces, "1,5 L x 6" or "duopack".
- The bol API integration has not been tested against the live API.
