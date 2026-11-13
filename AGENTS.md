# AGENTS.md

## Project map

This Python ETL publishes BIST financial statements from KAP, CPI and USD/TRY reference data, and İş Yatırım prices and historical capital to PostgreSQL.

- [pipeline.py](pipeline.py) defines the CLI. Use `python pipeline.py <command> --help` to check arguments and scope. `run` refreshes reference data, downloads KAP archives, and publishes financial data. `update-prices` fetches prices separately.
- [read_statements/](read_statements/) locates annual archives, parses KAP tables and EPS disclosures, and selects canonical facts. [data_mappings/](data_mappings/) holds statement mappings and the JSON seed for company metadata.
- [models/](models/) contains financial data structures and calculation logic. [services/](services/) handles ingestion, PostgreSQL publication, and recalculation.
- [database/migrations/](database/migrations/) contains the metadata bootstrap SQL and the Bronze/Silver/Gold/Ops migrations. Register new medallion migrations in `MIGRATIONS` in [services/postgres_financial_pipeline.py](services/postgres_financial_pipeline.py).
- [README.md](README.md) documents setup, CLI examples, and reporting views in Turkish and English. Update both sections when a change affects their documented behavior.

## Development and verification

Use Python 3.11+ and PostgreSQL 15+. Run commands from the repository root with the project virtual environment active.

```sh
python -m pip install -r requirements.txt
python -m pytest
ruff check .
```

- PostgreSQL integration tests use the fixtures in [tests/conftest.py](tests/conftest.py). Set `BIST_TEST_POSTGRES_DSN` to a test-server connection with `CREATEDB` privileges. The fixtures create and drop disposable databases. PostgreSQL tests are skipped when this variable is unset.
- The CLI connects through `BIST_FINANCE_DSN`, then `DATABASE_URL`, then the local `dbname=bist_finance` default. For CLI smoke tests that write data, explicitly set `BIST_FINANCE_DSN` to a disposable `bist_finance` database. Use `init-postgres` to apply the full schema and seed empty metadata. The optional `--create-database` flag uses the same connection settings against `postgres` and requires `CREATEDB` for a new database.
- Keep downloaded archives under `financial_statements/<SYMBOL>/<SYMBOL>_<YEAR>.zip` out of Git. Use retained archives and seeded reference data to test ingestion locally. `run --skip-download --skip-reference-data` reuses both inputs.

## Financial data and publication

Read the [financial-data instructions](.github/instructions/financial-data.instructions.md) before changing parsers, calculations, or database publication. They document source-specific cases and repair safeguards.

- Preserve Bronze source evidence, statement hierarchy, and reported quarter and YTD observations independently. Derive flows only from compatible fiscal dates, currencies, consolidation scopes, and measurement bases. Use actual fiscal period-end dates for CPI and FX, require complete CPI spans and appropriate period continuity, and leave unsupported results null.
- Use disclosed EPS units and period-specific weighted-average denominators. Derive quarters from earnings and share-day inputs when supported. Statement monetary multipliers and current company share metadata are unsuitable for historical EPS.
- Publish verified total basic ordinary-share EPS first and use the corresponding historical-capital estimate as a fallback for each quarter, YTD, and TTM field in `eps`. Keep detailed verified observations and estimate evidence in `gold.current_eps_values` and `gold.current_eps_coverage`, respectively. Rebase `eps_inflation_adj` from the selected field's own purchasing-power basis.
- Historical P/E uses the original İş Yatırım close (`HG_KAPANIS`) on or within ten calendar days before the actual fiscal period end. Keep verified P/E in the financial ratios and estimated P/E with its quality in `gold.current_price_earnings`. The valuation is retrospective.
- Use Gold `current_*` views to inspect active outputs. Preserve Bronze/Silver history and publication transaction boundaries. Before a production repair, back up active publication pointers and the derived data in scope. Limit repair republication to companies with active Gold data unless the user requests broader publication.
