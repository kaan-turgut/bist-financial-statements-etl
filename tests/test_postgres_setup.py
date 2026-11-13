import psycopg
from psycopg.rows import dict_row

import pipeline
from services import (
    import_company_metadata,
    postgres_financial_pipeline,
    postgres_setup,
)


def test_init_postgres_bootstraps_empty_database_and_preserves_metadata(
    test_database_dsn, monkeypatch
):
    def connect():
        return psycopg.connect(test_database_dsn, row_factory=dict_row)

    for module in (postgres_setup, postgres_financial_pipeline, import_company_metadata):
        monkeypatch.setattr(module, "connect_metadata_db", connect)

    pipeline.main(["init-postgres"])

    with connect() as conn:
        for relation in (
            "metadata.companies",
            "metadata.company_sectors",
            "metadata.company_indexes",
            "metadata.share_info",
            "metadata.non_traded_company_codes",
            "metadata.company_symbol_aliases",
            "bronze.statement_filings",
            "silver.current_financial_observations",
            "gold.current_financial_ratios",
            "ops.pipeline_runs",
        ):
            assert conn.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS installed", (relation,)
            ).fetchone()["installed"], relation

        versions = conn.execute(
            "SELECT version FROM ops.schema_migrations ORDER BY version"
        ).fetchall()
        assert [row["version"] for row in versions] == [
            version for version, _ in postgres_financial_pipeline.MIGRATIONS
        ]
        assert conn.execute(
            "SELECT EXISTS (SELECT 1 FROM metadata.non_traded_company_codes) AS seeded"
        ).fetchone()["seeded"]
        company = conn.execute(
            "UPDATE metadata.companies SET city_name = 'Local update' "
            "WHERE symbol = 'THYAO' RETURNING company_id, city_name"
        ).fetchone()
        assert company is not None

    pipeline.main(["init-postgres"])

    with connect() as conn:
        assert conn.execute(
            "SELECT company_id, city_name FROM metadata.companies WHERE symbol = 'THYAO'"
        ).fetchone() == company
        assert conn.execute(
            "SELECT version FROM ops.schema_migrations ORDER BY version"
        ).fetchall() == versions
