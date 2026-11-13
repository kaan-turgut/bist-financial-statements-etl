"""Initialize a PostgreSQL database from the checked-in schema and metadata."""

import logging

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from paths import PROJECT_ROOT
from services.import_company_metadata import import_company_metadata
from services.metadata_store import DATABASE_NAME, connect_metadata_db, metadata_dsn
from services.postgres_financial_pipeline import apply_medallion_migration

logger = logging.getLogger(__name__)

METADATA_MIGRATIONS = (
    "001_create_companies.sql",
    "001_create_company_sectors.sql",
    "003_create_company_indexes.sql",
    "004_create_share_info.sql",
    "005_create_non_traded_company_codes.sql",
    "006_create_company_symbol_aliases.sql",
)


def _create_database_if_missing():
    dsn = metadata_dsn()
    if conninfo_to_dict(dsn).get("dbname") != DATABASE_NAME:
        raise ValueError(f"The PostgreSQL connection must specify dbname={DATABASE_NAME} to create it.")

    with psycopg.connect(make_conninfo(dsn, dbname="postgres"), autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext('bist_finance_create_database'))")
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE_NAME,)).fetchone():
            return
        try:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE_NAME)))
        except psycopg.errors.DuplicateDatabase:
            return
        logger.info("Created PostgreSQL database %s", DATABASE_NAME)


def initialize_postgres(*, create_database=False):
    if create_database:
        _create_database_if_missing()

    with connect_metadata_db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('bist_finance_metadata_setup'))")
        migrations_dir = PROJECT_ROOT / "database" / "migrations"
        for filename in METADATA_MIGRATIONS:
            conn.execute((migrations_dir / filename).read_text(encoding="utf-8"))

    apply_medallion_migration()
    companies, non_traded = import_company_metadata(only_if_empty=True)
    if companies or non_traded:
        logger.info("Imported %s companies and %s non-traded codes", companies, non_traded)
    logger.info("PostgreSQL schema is ready in %s", DATABASE_NAME)
