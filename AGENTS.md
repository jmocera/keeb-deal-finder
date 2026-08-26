# Repository Instructions

## Commands

- Install dependencies with `python -m pip install -r requirements.txt`.
- Run the complete required suite with `python -m unittest discover -s tests -p "test_*.py"`.
- Run one focused test with `python -m unittest tests.test_categorizer.JsonParseTests.test_valid_json`.
- Run the manual live categorizer evaluation with `python -m unittest tests.test_categorizer_eval -v`; it requires `OPENROUTER_API_KEY` and targets at least 90% accuracy.
- Run a Shopify source preview with `python -m deal_bot.sources.shopify --dry-run --store KBDfans`.
- Preview the weekly digest without posting or database mutation with `python -m deal_bot.weekly_digest --dry-run`.
- Run the production pipeline with `python -m deal_bot`; `--loop` is for local testing only.
- Run the watchdog with `python -m deal_bot.watchdog`.

## Toolchain

- CI uses Python 3.13.
- Tests use stdlib `unittest`; there is no required pytest, lint, formatter, or typecheck configuration.
- `.github/workflows/deal_bot.yml` runs tests, then runs the bot with `if: always()`, so a test failure does not prevent the production step from executing.

## Configuration

- `deal_bot/config.py` loads the repository-root `.env` via `python-dotenv` at import time.
- Never commit `.env`, API keys, Supabase service keys, Discord webhook URLs, Bluesky credentials, or OpenRouter credentials. Use `.env` locally and GitHub Actions Secrets/Variables in CI.
- Local `.env` can make `tests/test_categorizer_eval.py` call the live OpenRouter API during test discovery; CI skips it because the test step does not receive `OPENROUTER_API_KEY`.
- `supabase_schema.sql` is the authoritative idempotent schema for `seen_deals`, `price_history`, `run_log`, and `posted_deals`; Supabase state is required because GitHub Actions runners are ephemeral.

## Architecture

- The executable entrypoint is `python -m deal_bot`, implemented by `deal_bot/__main__.py` delegating to `deal_bot/pipeline.py:main`.
- `pipeline.run_once()` fetches sources, deduplicates by `deal["id"]`, records price history, applies deterministic discount/savings/historical-low gates, enriches candidates with AI, posts to Discord, and records Supabase state.
- Preserve the pipeline merge, dedupe, deterministic gating, posting, and retry behavior when changing sources.
- Sources must emit the `deal_bot.sources.base.Deal` TypedDict shape: `id`, `source`, `title`, `url`, `image`, `list_price`, `sale_price`, and `discount_pct`.
- Read-only HTTP calls must use `deal_bot.transport.request`; Discord and Bluesky POSTs intentionally use their own logic because automatic retries can duplicate public posts.
- Source webhook routing is by `deal["source"]` through `config.SOURCE_WEBHOOKS`; the AI categorizer does not route posts.

## AI Safety

- The categorizer, quality scorer, and desirability classifier are shadow/reporting features unless explicitly documented otherwise.
- `categorize_deals()` must preserve its return contract `(dict[deal_id, category], model_name_or_none)`.
- Categorizer parse/model failures must return `({}, None)` and must never gate, drop, edit, or route deals.
- Keep AI output validation strict and fail-safe. Do not salvage partial model responses.
- `CLASSIFIER_MODE` is separate from the categorizer and may be `shadow` or `gate`; do not change it while working on categorization unless explicitly requested.

## Shopify Source

- `deal_bot/sources/shopify.py` reads the config-driven `SHOPIFY_STORES` JSON list and uses public Shopify `/products.json` or `/collections/{handle}/products.json` endpoints.
- Shopify deals must have a positive price and an available variant with `compare_at_price > price`; products without a real markdown must be dropped at the source.
- Keep Shopify requests single-page, throttled between stores, and isolated per store. Use `?limit=250`; pagination is currently an explicit coverage gap.
- Kinetic Labs is not in the default Shopify list because its tested endpoint did not provide compatible Shopify JSON. Do not add it back without re-verifying the endpoint.
- Keep Shopify-specific tests hermetic by mocking `deal_bot.sources.shopify.transport.request`.

## Documentation

- `HANDOFF.md`, `BACKLOG.md`, and `VoltDrop_Project_Scope.md` contain historical claims that may mention the former PC/gaming/Steam implementation. Trust executable code, current tests, `.env.example`, and workflows when prose conflicts.
