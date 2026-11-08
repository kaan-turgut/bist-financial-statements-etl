"""Isolated PostgreSQL fixtures shared by ETL integration tests."""

import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from paths import PROJECT_ROOT
from services import postgres_financial_pipeline as pipeline


@pytest.fixture(scope="module")
def test_database_dsn():
    admin_dsn = os.environ.get("BIST_TEST_POSTGRES_DSN")
    if not admin_dsn:
        pytest.skip("Set BIST_TEST_POSTGRES_DSN to run isolated PostgreSQL integration tests")
    database_name = "bist_etl_test_" + uuid.uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database_name)))
        try:
            yield make_conninfo(admin_dsn, dbname=database_name)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))


@pytest.fixture
def legacy_database(test_database_dsn, monkeypatch):
    def connect():
        return psycopg.connect(test_database_dsn, row_factory=dict_row)

    monkeypatch.setattr(pipeline, "connect_metadata_db", connect)
    with connect() as conn:
        for schema in ("gold", "silver", "bronze", "ops", "metadata"):
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        conn.execute("""CREATE SCHEMA metadata;
            CREATE TABLE metadata.companies (
                company_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                symbol text NOT NULL UNIQUE, mkk_member_oid text NOT NULL UNIQUE,
                paid_in_capital numeric(20,2))""")
        for filename in ("004_create_share_info.sql", "007_create_medallion_architecture.sql"):
            conn.execute((PROJECT_ROOT / "database" / "migrations" / filename).read_text())
        conn.execute("INSERT INTO ops.schema_migrations (version) VALUES ('007')")
    return connect


@pytest.fixture
def database(legacy_database):
    assert pipeline.apply_medallion_migration()
    with legacy_database() as conn:
        company_id = conn.execute("""INSERT INTO metadata.companies
            (symbol,mkk_member_oid,paid_in_capital) VALUES ('TEST','test-oid',10)
            RETURNING company_id""").fetchone()["company_id"]
        run_id = pipeline._run_start(conn, "test", {})
        conn.execute("""INSERT INTO silver.monthly_macro_data
            (series_code,year,month,observation_date,value,source)
            SELECT 'CPI_MONTHLY_RATE',EXTRACT(YEAR FROM d),EXTRACT(MONTH FROM d),d,0,'test'
            FROM generate_series('2022-01-01'::date,'2026-12-01'::date,interval '1 month') d""")
        conn.commit()
        yield conn, company_id, run_id
