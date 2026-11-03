"""One-time import of the legacy company JSON files into PostgreSQL."""

import json

from paths import DATA_MAPPINGS_DIR
from services.metadata_store import (
    _parse_timestamp,
    connect_metadata_db,
    upsert_company_metadata,
    upsert_non_traded_metadata,
)


def import_company_metadata(
    company_info_path=DATA_MAPPINGS_DIR / "company_info.json",
    non_traded_path=DATA_MAPPINGS_DIR / "non_traded_company_codes.json",
):
    with open(company_info_path, encoding="utf-8") as file:
        companies = json.load(file)
    with open(non_traded_path, encoding="utf-8") as file:
        non_traded = json.load(file)

    conn = connect_metadata_db()
    try:
        for symbol, info in companies.items():
            scraped_at = _parse_timestamp(info.get("lastUpdated"))
            upsert_company_metadata(conn, symbol, info, scraped_at=scraped_at)
        for symbol, record in non_traded.items():
            upsert_non_traded_metadata(conn, symbol, record)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return len(companies), len(non_traded)


if __name__ == "__main__":
    company_count, non_traded_count = import_company_metadata()
    print(
        f"Imported {company_count} companies and {non_traded_count} non-traded codes "
        "into bist_finance."
    )
