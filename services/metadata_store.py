"""PostgreSQL storage for company and KAP metadata."""

import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

DATABASE_NAME = "bist_finance"


def _executemany(conn, query, rows):
    with conn.cursor() as cursor:
        cursor.executemany(query, rows)


def metadata_dsn():
    return (
        os.environ.get("BIST_FINANCE_DSN")
        or os.environ.get("DATABASE_URL")
        or f"dbname={DATABASE_NAME}"
    )


def connect_metadata_db():
    conn = psycopg.connect(metadata_dsn(), row_factory=dict_row)
    if conn.info.dbname != DATABASE_NAME:
        conn.close()
        raise RuntimeError(
            f"Connected to database {conn.info.dbname!r}; expected {DATABASE_NAME!r}."
        )
    return conn


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for date_format in ("%d.%m.%Y", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=timezone.utc).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"Unrecognized KAP date value: {value!r}") from exc


def _parse_timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        for date_format in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, date_format).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    raise ValueError(f"Unrecognized KAP timestamp value: {value!r}")


def _parse_local_timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).replace(tzinfo=None)
        except ValueError:
            pass
    for date_format in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, date_format)  # noqa: DTZ007
        except ValueError:
            pass
    raise ValueError(f"Unrecognized KAP trade registration date: {value!r}")


def load_company_records(conn):
    """Read company rows and their normalized metadata into ticker-keyed records."""
    rows = conn.execute(
        """
        SELECT
            company_id, symbol, mkk_member_oid, company_code,
            kap_member_title, related_member_title, related_member_oid, city_name,
            kap_member_type, kap_member_state, financial_type, activity_state,
            trading_state, tax_no, tax_office, trade_reg_no, trade_reg_office,
            trade_reg_date, bist_market, instrument_type, instrument_listing_date,
            instrument_country, instrument_exchange, instrument_market, website,
            activity_field, activity_status_text, independent_audit_company,
            paid_in_capital, capital_ceiling, kap_summary_url, kap_general_url,
            source, last_scraped_at
        FROM metadata.companies
        ORDER BY symbol
        """
    ).fetchall()

    companies = {}
    for row in rows:
        scraped_at = row["last_scraped_at"]
        companies[row["symbol"]] = {
            "mkkMemberOid": row["mkk_member_oid"],
            "companyCode": row["company_code"],
            "freeFloatSymbol": row["symbol"],
            "kapMemberTitle": row["kap_member_title"],
            "relatedMemberTitle": row["related_member_title"],
            "relatedMemberOid": row["related_member_oid"],
            "cityName": row["city_name"],
            "kapMemberType": row["kap_member_type"],
            "kapMemberState": row["kap_member_state"],
            "financialType": row["financial_type"],
            "faaliyetDurumu": row["activity_state"],
            "payIslemDurumu": row["trading_state"],
            "taxNo": row["tax_no"],
            "taxOffice": row["tax_office"],
            "tradeRegNo": row["trade_reg_no"],
            "tradeRegOffice": row["trade_reg_office"],
            "tradeRegDate": row["trade_reg_date"],
            "bistMarket": row["bist_market"],
            "capitalMarketInstrumentType": row["instrument_type"],
            "capitalMarketInstrumentListingDate": row["instrument_listing_date"],
            "capitalMarketInstrumentCountry": row["instrument_country"],
            "capitalMarketInstrumentExchange": row["instrument_exchange"],
            "capitalMarketInstrumentMarket": row["instrument_market"],
            "Website": row["website"],
            "activityField": row["activity_field"],
            "activityStatusText": row["activity_status_text"],
            "independentAuditCompany": row["independent_audit_company"],
            "paidInCapital": row["paid_in_capital"],
            "capitalCeiling": row["capital_ceiling"],
            "numSharesOutstanding": None,
            "hasTradedShares": row["trading_state"] == "1",
            "capitalDetails": None,
            "kapSummaryUrl": row["kap_summary_url"],
            "kapGeneralUrl": row["kap_general_url"],
            "source": row["source"],
            "lastUpdated": scraped_at.isoformat() if scraped_at else None,
            "Sectors": [],
            "Indexes": [],
            "shareClasses": [],
            "symbolAliases": [],
        }

    children = (
        ("company_sectors", "sector_name", "Sectors"),
        ("company_indexes", "index_name", "Indexes"),
    )
    for table, value_column, record_key in children:
        child_rows = conn.execute(
            f"""SELECT c.symbol, child.{value_column} AS value
                FROM metadata.{table} AS child
                JOIN metadata.companies AS c USING (company_id)
                ORDER BY c.symbol, child.{value_column}"""
        ).fetchall()
        for child in child_rows:
            if child["symbol"] in companies:
                companies[child["symbol"]][record_key].append(child["value"])

    for row in conn.execute(
        """
        SELECT c.symbol, s.symbol AS share_symbol, s.total_shares,
               s.non_traded_shares, s.traded_shares,
               s.excluded_traded_shares_ic, s.excluded_traded_shares_ide,
               s.free_float_shares, s.free_float_ratio
        FROM metadata.share_info AS s
        JOIN metadata.companies AS c USING (company_id)
        ORDER BY c.symbol, s.symbol
        """
    ).fetchall():
        companies[row["symbol"]]["shareClasses"].append(
            {
                "symbol": row["share_symbol"],
                "totalShares": row["total_shares"],
                "nonTradedShares": row["non_traded_shares"],
                "tradedShares": row["traded_shares"],
                "excludedTradedSharesIC": row["excluded_traded_shares_ic"],
                "excludedTradedSharesIDE": row["excluded_traded_shares_ide"],
                "freeFloatShares": row["free_float_shares"],
                "freeFloatRatio": row["free_float_ratio"],
            }
        )

    for company in companies.values():
        total_shares = [
            share["totalShares"]
            for share in company["shareClasses"]
            if isinstance(share["totalShares"], (int, float, Decimal))
        ]
        if total_shares:
            company["numSharesOutstanding"] = float(sum(total_shares))
        else:
            paid_in_capital = company["paidInCapital"]
            company["numSharesOutstanding"] = (
                float(paid_in_capital) if paid_in_capital is not None else None
            )

    for row in conn.execute(
        """
        SELECT c.symbol, a.alias_symbol
        FROM metadata.company_symbol_aliases AS a
        JOIN metadata.companies AS c USING (company_id)
        ORDER BY c.symbol, a.alias_symbol
        """
    ).fetchall():
        companies[row["symbol"]]["symbolAliases"].append(row["alias_symbol"])

    return companies


def load_non_traded_records(conn):
    rows = conn.execute(
        """
        SELECT symbol, kap_summary_url, kap_general_url, last_checked_at
        FROM metadata.non_traded_company_codes
        ORDER BY symbol
        """
    ).fetchall()
    return {
        row["symbol"]: {
            "kapSummaryUrl": row["kap_summary_url"],
            "kapGeneralUrl": row["kap_general_url"],
            "lastChecked": row["last_checked_at"].isoformat() if row["last_checked_at"] else None,
        }
        for row in rows
    }


def upsert_company_metadata(conn, symbol, info, *, scraped_at=None):
    oid = info.get("mkkMemberOid")
    if not oid:
        raise ValueError(f"Cannot store {symbol}: KAP did not provide mkkMemberOid.")

    values = {
        "symbol": symbol,
        "mkk_member_oid": oid,
        "company_code": info.get("companyCode"),
        "kap_member_title": info.get("kapMemberTitle"),
        "related_member_title": info.get("relatedMemberTitle"),
        "related_member_oid": info.get("relatedMemberOid"),
        "city_name": info.get("cityName"),
        "kap_member_type": info.get("kapMemberType"),
        "kap_member_state": info.get("kapMemberState"),
        "financial_type": info.get("financialType"),
        "activity_state": info.get("faaliyetDurumu"),
        "trading_state": info.get("payIslemDurumu"),
        "tax_no": info.get("taxNo"),
        "tax_office": info.get("taxOffice"),
        "trade_reg_no": info.get("tradeRegNo"),
        "trade_reg_office": info.get("tradeRegOffice"),
        "trade_reg_date": _parse_local_timestamp(info.get("tradeRegDate")),
        "bist_market": info.get("bistMarket"),
        "instrument_type": info.get("capitalMarketInstrumentType"),
        "instrument_listing_date": _parse_date(info.get("capitalMarketInstrumentListingDate")),
        "instrument_country": info.get("capitalMarketInstrumentCountry"),
        "instrument_exchange": info.get("capitalMarketInstrumentExchange"),
        "instrument_market": info.get("capitalMarketInstrumentMarket"),
        "website": info.get("Website"),
        "activity_field": info.get("activityField"),
        "activity_status_text": info.get("activityStatusText"),
        "independent_audit_company": info.get("independentAuditCompany"),
        "paid_in_capital": info.get("paidInCapital"),
        "capital_ceiling": info.get("capitalCeiling"),
        "kap_summary_url": info.get("kapSummaryUrl"),
        "kap_general_url": info.get("kapGeneralUrl"),
        "source": info.get("source", "KAP"),
        "last_scraped_at": scraped_at or datetime.now(timezone.utc),
    }
    columns = list(values)
    assignments = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in columns if column != "mkk_member_oid"
    )
    placeholders = ", ".join(["%s"] * len(columns))
    row = conn.execute(
        f"""INSERT INTO metadata.companies ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT (mkk_member_oid) DO UPDATE SET {assignments}
            RETURNING company_id""",
        list(values.values()),
    ).fetchone()
    company_id = row["company_id"]

    aliases = sorted(
        {
            re.sub(r"[^A-Z0-9]", "", alias.strip().upper())
            for alias in info.get("symbolAliases", [])
            if alias and re.sub(r"[^A-Z0-9]", "", alias.strip().upper()) != symbol.upper()
        }
    )
    conflicts = conn.execute(
        """
        SELECT symbol FROM metadata.companies
        WHERE symbol = ANY(%s) AND company_id <> %s
        UNION
        SELECT alias_symbol AS symbol FROM metadata.company_symbol_aliases
        WHERE (alias_symbol = ANY(%s) OR alias_symbol = %s) AND company_id <> %s
        """,
        (aliases, company_id, aliases, symbol.upper(), company_id),
    ).fetchall()
    if conflicts:
        raise ValueError(
            f"Cannot store aliases for {symbol}; already assigned elsewhere: "
            + ", ".join(row["symbol"] for row in conflicts)
        )

    for table in ("company_sectors", "company_indexes", "share_info", "company_symbol_aliases"):
        conn.execute(f"DELETE FROM metadata.{table} WHERE company_id = %s", (company_id,))

    _executemany(
        conn,
        "INSERT INTO metadata.company_sectors (company_id, sector_name) VALUES (%s, %s)",
        [(company_id, value) for value in dict.fromkeys(info.get("Sectors") or []) if value],
    )
    _executemany(
        conn,
        "INSERT INTO metadata.company_indexes (company_id, index_name) VALUES (%s, %s)",
        [(company_id, value) for value in dict.fromkeys(info.get("Indexes") or []) if value],
    )
    _executemany(
        conn,
        """
        INSERT INTO metadata.share_info (
            company_id, symbol, total_shares, non_traded_shares, traded_shares,
            excluded_traded_shares_ic, excluded_traded_shares_ide,
            free_float_shares, free_float_ratio
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                company_id,
                share["symbol"],
                share.get("totalShares"),
                share.get("nonTradedShares"),
                share.get("tradedShares"),
                share.get("excludedTradedSharesIC"),
                share.get("excludedTradedSharesIDE"),
                share.get("freeFloatShares"),
                share.get("freeFloatRatio"),
            )
            for share in info.get("shareClasses") or []
            if share.get("symbol")
        ],
    )
    _executemany(
        conn,
        "INSERT INTO metadata.company_symbol_aliases (company_id, alias_symbol) VALUES (%s, %s)",
        [(company_id, alias) for alias in aliases],
    )
    return company_id


def upsert_non_traded_metadata(conn, symbol, record):
    summary_url = record.get("kapSummaryUrl")
    general_url = record.get("kapGeneralUrl")
    if not summary_url or not general_url:
        raise ValueError(f"Cannot cache non-traded code {symbol}: KAP URLs are missing.")
    conn.execute(
        """
        INSERT INTO metadata.non_traded_company_codes (
            symbol, kap_summary_url, kap_general_url, last_checked_at
        ) VALUES (%s, %s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET
            kap_summary_url = EXCLUDED.kap_summary_url,
            kap_general_url = EXCLUDED.kap_general_url,
            last_checked_at = EXCLUDED.last_checked_at
        """,
        (
            symbol,
            summary_url,
            general_url,
            _parse_timestamp(record.get("lastChecked")) or datetime.now(timezone.utc),
        ),
    )


def delete_non_traded_codes(conn, symbols):
    normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    if normalized:
        conn.execute(
            "DELETE FROM metadata.non_traded_company_codes WHERE symbol = ANY(%s)",
            (normalized,),
        )
