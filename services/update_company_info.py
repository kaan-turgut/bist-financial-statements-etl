import argparse
import copy
import json
import logging
import re
from collections import OrderedDict
from time import sleep
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from logging_config import configure_logging
from services.metadata_store import (
    connect_metadata_db,
    delete_non_traded_codes,
    load_company_records,
    load_non_traded_records,
    upsert_company_metadata,
    upsert_non_traded_metadata,
)

logger = logging.getLogger(__name__)

KAP_BASE_URL = "https://www.kap.org.tr"
BIST_COMPANIES_URL = f"{KAP_BASE_URL}/tr/bist-sirketler"
MISSING_VALUE_TEXT = "Bilgi Mevcut Değil"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Refresh company and non-traded-member metadata in PostgreSQL from KAP."
    )
    parser.add_argument("--limit", type=int, help="Process only the first N companies for testing.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        help="Refresh only these existing ticker(s), resolved through their KAP company code.",
    )
    parser.add_argument("--sleep", type=float, default=1.5, help="Seconds to sleep between company requests.")
    parser.add_argument("--retries", type=int, default=6, help="HTTP attempts per KAP page.")
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=5.0,
        help="Base seconds for exponential retry delays after transient HTTP failures.",
    )
    parser.add_argument(
        "--rate-limit-backoff",
        type=float,
        default=300.0,
        help="Base seconds for exponential retry delays after KAP request-limit responses.",
    )
    parser.add_argument(
        "--include-non-traded",
        action="store_true",
        help="Include KAP/BIST members even when no traded share evidence is found.",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Fetch detail pages only for KAP list symbols missing from PostgreSQL metadata.",
    )
    parser.add_argument(
        "--recheck-non-traded",
        action="store_true",
        help="In missing-only mode, fetch detail pages even for members cached as non-share-traded.",
    )
    parser.add_argument(
        "--start-after",
        help="Resume after this list code or stock symbol, for example BEGYO.",
    )
    return parser.parse_args()


def fetch_html(session, url, retries=6, retry_backoff=5.0, rate_limit_backoff=300.0):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        "Referer": KAP_BASE_URL,
    }

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=30, headers=headers)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                retry_after = response.headers.get("Retry-After")
                backoff = rate_limit_backoff if response.status_code == 429 else retry_backoff
                delay = float(retry_after) if retry_after and retry_after.isdecimal() else backoff * attempt
                logger.warning(
                    "KAP returned HTTP %s for %s; retrying in %.1fs (%s/%s)",
                    response.status_code,
                    url,
                    delay,
                    attempt,
                    retries,
                )
                sleep(delay)
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError:
                body_preview = response.text[:300].replace("\n", " ").strip()
                logger.error(
                    "KAP request failed: HTTP %s for %s; response preview: %s",
                    response.status_code,
                    url,
                    body_preview,
                )
                raise

            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                break

            delay = retry_backoff * attempt
            logger.warning(
                "KAP request error for %s: %s; retrying in %.1fs (%s/%s)",
                url,
                exc,
                delay,
                attempt,
                retries,
            )
            sleep(delay)

    raise last_error


def discover_bist_company_urls(session, retries=6, retry_backoff=5.0, rate_limit_backoff=300.0):
    html = fetch_html(
        session,
        BIST_COMPANIES_URL,
        retries=retries,
        retry_backoff=retry_backoff,
        rate_limit_backoff=rate_limit_backoff,
    )
    soup = BeautifulSoup(html, "html.parser")
    links = OrderedDict()

    for row in soup.select("tr"):
        anchor = row.select_one('td:first-child a[href*="/tr/sirket-bilgileri/ozet/"]')
        if not anchor:
            continue
        href = anchor.get("href")
        text = anchor.get_text(" ", strip=True)
        if not href or not text:
            continue
        url = urljoin(KAP_BASE_URL, href)
        links.setdefault(url, text)

    return list(links.items())


def text_lines(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    return [line.strip() for line in text.splitlines() if line.strip()]


def value_after(lines, label):
    try:
        value = lines[lines.index(label) + 1]
    except (ValueError, IndexError):
        return None
    return None if value.strip() == MISSING_VALUE_TEXT else value


def normalize_missing_values(value):
    if isinstance(value, str) and value.strip() == MISSING_VALUE_TEXT:
        return None
    if isinstance(value, dict):
        return {key: normalize_missing_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_missing_values(item) for item in value]
    return value


def parse_number(value):
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned or cleaned == MISSING_VALUE_TEXT:
        return None

    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError:
        return value

    return int(number) if number.is_integer() else number


def extract_member_detail(summary_html):
    match = re.search(r'\\"memberDetail\\":(\{.*?\}),\\"companyDocuments\\"', summary_html)
    if not match:
        return {}

    decoded = json.loads(f'"{match.group(1)}"')
    return json.loads(decoded)


def extract_free_float_symbol(lines):
    return extract_free_float_info(lines)["symbol"]


def normalize_symbol(symbol):
    return re.sub(r"[^A-Z0-9]", "", symbol.strip().upper())


def normalize_list_symbols(list_code):
    return {
        normalize_symbol(symbol)
        for symbol in re.split(r"[\s,]+", list_code)
        if normalize_symbol(symbol)
    }


def extract_free_float_rows(lines):
    try:
        idx = lines.index("Fiili Dolaşımdaki Paylar")
    except ValueError:
        return []

    table_lines = lines[idx:idx + 200]
    if "Bilgi Mevcut Değil" in table_lines:
        return []

    try:
        ratio_header_idx = next(
            i for i, line in enumerate(table_lines)
            if line.startswith("Fiili Dolaşımdaki Pay Oranı")
        )
        rows = []
        row_start = ratio_header_idx + 1
        while row_start + 7 < len(table_lines):
            raw_symbol = table_lines[row_start]
            symbol = normalize_symbol(raw_symbol)
            if not re.fullmatch(r"[A-Z0-9]{2,10}", symbol):
                break
            rows.append({
                "symbol": symbol,
                "totalShares": parse_number(table_lines[row_start + 1]),
                "nonTradedShares": parse_number(table_lines[row_start + 2]),
                "tradedShares": parse_number(table_lines[row_start + 3]),
                "excludedTradedSharesIC": parse_number(table_lines[row_start + 4]),
                "excludedTradedSharesIDE": parse_number(table_lines[row_start + 5]),
                "freeFloatShares": parse_number(table_lines[row_start + 6]),
                "freeFloatRatio": parse_number(table_lines[row_start + 7]),
            })
            row_start += 8
        return rows
    except StopIteration:
        return []


def extract_free_float_info(lines):
    rows = extract_free_float_rows(lines)
    if not rows:
        return {"symbol": None, "shares": None, "ratio": None}

    primary = choose_primary_share_class(rows)
    return {
        "symbol": primary["symbol"],
        "shares": primary["freeFloatShares"],
        "ratio": primary["freeFloatRatio"],
    }


def choose_primary_share_class(share_classes):
    def free_float_shares(row):
        value = row.get("freeFloatShares")
        return value if isinstance(value, (int, float)) else -1

    return max(share_classes, key=free_float_shares)


def remove_malformed_symbol_keys(companies):
    cleaned = {}
    for symbol, info in companies.items():
        normalized = normalize_symbol(symbol)
        if normalized == symbol:
            cleaned[symbol] = info
        elif normalized not in companies:
            cleaned[normalized] = info
    return cleaned


def extract_capital_market_instrument(lines, general_html=None):
    market = value_after(lines, "Sermaye Piyasası Aracının İşlem Gördüğü Pazar")
    empty = {
        "type": None,
        "listingDate": None,
        "country": None,
        "exchange": None,
        "market": market,
    }

    if general_html:
        soup = BeautifulSoup(general_html, "html.parser")
        for row in soup.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if not cells or normalize_symbol(cells[0]) != normalize_symbol("PAY SENEDİ"):
                continue
            cells += [""] * max(0, 5 - len(cells))
            return {
                "type": cells[0] or None,
                "listingDate": cells[1] or None,
                "country": cells[2] or None,
                "exchange": cells[3] or None,
                "market": cells[4] or market,
            }

    try:
        start = lines.index("PAY SENEDİ")
    except ValueError:
        return empty

    if len(lines) <= start + 1 or lines[start] == "Bilgi Mevcut Değil":
        return empty

    remaining = lines[start + 1:]
    country_values = {"TÜRKİYE", "TURKEY"}
    if remaining[0].upper() in country_values:
        # KAP sometimes emits an empty listing-date cell; text extraction drops it.
        listing_date = None
        country, exchange = remaining[:2]
        instrument_market = remaining[2] if len(remaining) > 2 else market
    else:
        listing_date = remaining[0]
        country = remaining[1] if len(remaining) > 1 else None
        exchange = remaining[2] if len(remaining) > 2 else None
        instrument_market = remaining[3] if len(remaining) > 3 else market

    return {
        "type": lines[start],
        "listingDate": listing_date,
        "country": country,
        "exchange": exchange,
        "market": instrument_market or market,
    }


def extract_capital_details(lines):
    try:
        start = lines.index("Borsada İşlem Görüp Görmediği(*)") + 1
    except ValueError:
        return {}

    values = []
    for line in lines[start:]:
        if line.startswith("(*) Pay grubunun"):
            break
        values.append(line)

    rows = []
    current = []
    for value in values:
        current.append(value)
        if value in {"İşlem Görüyor", "İşlem Görmüyor"}:
            rows.append(current)
            current = []

    details = {
        "Pay Grubu": [],
        "Nama / Hamiline": [],
        "Beher Payın Nominal Değeri (TL)": [],
        "Para Birimi": [],
        "Payların Nominal Değeri": [],
        "Sermayeye Oranı": [],
        "İmtiyaz Türü": [],
        "Borsada İşlem Görüp Görmediği": [],
    }

    for row in rows:
        if len(row) < 7:
            continue
        details["Pay Grubu"].append(row[0])
        details["Nama / Hamiline"].append(row[1])
        details["Beher Payın Nominal Değeri (TL)"].append(parse_number(row[2]))
        details["Para Birimi"].append(row[3])
        details["Payların Nominal Değeri"].append(parse_number(row[4]))
        details["Sermayeye Oranı"].append(parse_number(row[6] if len(row) > 7 else row[5]))
        details["İmtiyaz Türü"].append(row[-2] if len(row) > 8 else "")
        details["Borsada İşlem Görüp Görmediği"].append(row[-1])

    return details


def shares_are_traded(member_detail, capital_details, lines):
    if "İşlem Görüyor" in capital_details.get("Borsada İşlem Görüp Görmediği", []):
        return True
    if member_detail.get("payIslemDurumu") == "1":
        return True
    return "PAY SENEDİ" in lines and "İşlem Görüyor" in lines


def calculate_num_shares(capital_details, paid_in_capital):
    nominal_values = capital_details.get("Payların Nominal Değeri", [])
    per_share_values = capital_details.get("Beher Payın Nominal Değeri (TL)", [])

    total = 0
    for nominal, per_share in zip(nominal_values, per_share_values):
        if isinstance(nominal, (int, float)) and isinstance(per_share, (int, float)) and per_share:
            total += nominal / per_share

    if total:
        return float(total)

    return float(paid_in_capital) if isinstance(paid_in_capital, (int, float)) else None


def build_company_info(summary_html, general_html, summary_url=None, general_url=None, last_updated=None):
    member_detail = extract_member_detail(summary_html)
    lines = text_lines(general_html)

    sectors = (value_after(lines, "Şirketin Sektörü") or "").split(" / ")
    indexes = (value_after(lines, "Şirketin Dahil Olduğu Endeksler") or "").split(" / ")
    capital_details = extract_capital_details(lines)
    paid_in_capital = parse_number(value_after(lines, "Ödenmiş/Çıkarılmış Sermaye"))
    capital_ceiling = parse_number(value_after(lines, "Kayıtlı Sermaye Tavanı"))
    free_float_rows = extract_free_float_rows(lines)
    primary_share_class = choose_primary_share_class(free_float_rows) if free_float_rows else None
    free_float_info = {
        "symbol": primary_share_class["symbol"] if primary_share_class else None,
        "shares": primary_share_class["freeFloatShares"] if primary_share_class else None,
        "ratio": primary_share_class["freeFloatRatio"] if primary_share_class else None,
    }
    capital_market_instrument = extract_capital_market_instrument(lines, general_html)
    stock_code_raw = member_detail.get("stockCode") or ""
    stock_symbols = sorted(normalize_list_symbols(stock_code_raw))
    stock_code = free_float_info["symbol"]
    if not stock_code and len(stock_symbols) == 1:
        stock_code = stock_symbols[0]
    has_traded_shares = shares_are_traded(member_detail, capital_details, lines)

    info = {
        "mkkMemberOid": member_detail.get("mkkMemberOid"),
        "kapMemberTitle": member_detail.get("kapMemberTitle"),
        "relatedMemberTitle": member_detail.get("relatedMemberTitle"),
        "cityName": member_detail.get("cityName"),
        "relatedMemberOid": member_detail.get("relatedMemberOid"),
        "kapMemberType": member_detail.get("kapMemberType"),
        "kapMemberState": member_detail.get("kapMemberState"),
        "financialType": member_detail.get("financialType"),
        "faaliyetDurumu": member_detail.get("faaliyetDurumu"),
        "payIslemDurumu": member_detail.get("payIslemDurumu"),
        "freeFloatSymbol": free_float_info["symbol"],
        "companyCode": member_detail.get("companyCode"),
        "taxNo": member_detail.get("taxNo"),
        "taxOffice": member_detail.get("taxOffice"),
        "tradeRegNo": member_detail.get("tradeRegNo"),
        "tradeRegOffice": member_detail.get("tradeRegOffice"),
        "tradeRegDate": member_detail.get("tradeRegDate"),
        "Sectors": [sector for sector in sectors if sector],
        "Indexes": [index for index in indexes if index],
        "bistMarket": capital_market_instrument["market"],
        "capitalMarketInstrumentType": capital_market_instrument["type"],
        "capitalMarketInstrumentListingDate": capital_market_instrument["listingDate"],
        "capitalMarketInstrumentCountry": capital_market_instrument["country"],
        "capitalMarketInstrumentExchange": capital_market_instrument["exchange"],
        "capitalMarketInstrumentMarket": capital_market_instrument["market"],
        "Website": value_after(lines, "İnternet Adresi"),
        "activityField": value_after(lines, "Şirketin Faaliyet Konusu"),
        "activityStatusText": value_after(lines, "Faaliyet Durumu"),
        "independentAuditCompany": value_after(lines, "Bağımsız Denetim Kuruluşu"),
        "paidInCapital": paid_in_capital,
        "capitalCeiling": capital_ceiling,
        "capitalDetails": capital_details,
        "shareClasses": free_float_rows,
        "symbolAliases": sorted(
            (set(stock_symbols) | {row["symbol"] for row in free_float_rows}) - {stock_code}
        ) if stock_code else sorted(set(stock_symbols) | {row["symbol"] for row in free_float_rows}),
        "numSharesOutstanding": calculate_num_shares(capital_details, paid_in_capital),
        "hasTradedShares": has_traded_shares,
        "kapSummaryUrl": summary_url,
        "kapGeneralUrl": general_url,
        "source": "KAP",
        "lastUpdated": last_updated,
    }

    infos_by_symbol = {}
    if stock_code:
        infos_by_symbol[stock_code] = info

    info = normalize_missing_values(info)
    if stock_code:
        infos_by_symbol[stock_code] = info

    return stock_code, info, has_traded_shares, infos_by_symbol


def consolidate_company_info_by_oid(companies):
    """Collapse duplicate keys for one MKK member while retaining class/code metadata."""
    groups = {}
    for symbol, info in companies.items():
        oid = info.get("mkkMemberOid")
        if oid:
            groups.setdefault(oid, []).append((symbol, info))

    consolidated = dict(companies)
    removed = 0
    for oid, entries in groups.items():
        if len(entries) < 2:
            continue

        all_symbols = {symbol for symbol, _ in entries}
        traded_symbols = {
            info.get("freeFloatSymbol")
            for _, info in entries
            if info.get("freeFloatSymbol")
        }
        if not traded_symbols:
            logger.warning("Keeping duplicate MKK OID %s: no verified traded symbol to select", oid)
            continue

        def traded_share_count(candidate, company_entries=entries):
            values = [
                share_class.get("freeFloatShares")
                for _, info in company_entries
                for share_class in info.get("shareClasses") or []
                if share_class.get("symbol") == candidate
            ]
            numeric = [value for value in values if isinstance(value, (int, float))]
            return max(numeric, default=-1)

        canonical = min(
            traded_symbols,
            key=lambda candidate: (-traded_share_count(candidate), candidate),
        )
        canonical_entry = next(
            ((symbol, info) for symbol, info in entries if symbol == canonical),
            max(entries, key=lambda entry: entry[1].get("lastUpdated") or ""),
        )
        merged = copy.deepcopy(canonical_entry[1])

        classes_by_symbol = {}
        for _, info in entries:
            for share_class in info.get("shareClasses") or []:
                if share_class.get("symbol"):
                    classes_by_symbol[share_class["symbol"]] = share_class
            if info.get("freeFloatSymbol"):
                classes_by_symbol.setdefault(info["freeFloatSymbol"], {"symbol": info["freeFloatSymbol"]})

        aliases = set(merged.get("symbolAliases") or [])
        aliases.update(all_symbols - {canonical})
        aliases.update(traded_symbols - {canonical})
        merged["freeFloatSymbol"] = canonical
        merged["symbolAliases"] = sorted(aliases)
        merged["shareClasses"] = sorted(classes_by_symbol.values(), key=lambda item: item["symbol"])

        for symbol, _ in entries:
            if symbol != canonical:
                consolidated.pop(symbol, None)
                removed += 1
        consolidated[canonical] = merged

    return consolidated, removed


def refresh_company_info(limit=None, sleep_seconds=3.0, include_non_traded=False, retries=6,
                         retry_backoff=5.0, rate_limit_backoff=300.0, missing_only=False,
                         recheck_non_traded=False, start_after=None, symbols=None):
    from datetime import UTC, datetime

    session = requests.Session()
    company_links = discover_bist_company_urls(
        session,
        retries=retries,
        retry_backoff=retry_backoff,
        rate_limit_backoff=rate_limit_backoff,
    )
    conn = connect_metadata_db()
    companies = load_company_records(conn)
    non_traded_cache = load_non_traded_records(conn)
    conn.commit()
    if companies:
        logger.info("Loaded %s companies from bist_finance.metadata", len(companies))

    if symbols:
        requested = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        selected_codes = set()
        for requested_symbol in requested:
            matches = [
                (key, info)
                for key, info in companies.items()
                if key.upper() == requested_symbol
                or requested_symbol in {alias.upper() for alias in info.get("symbolAliases", [])}
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"Expected one PostgreSQL company record for {requested_symbol}; found {len(matches)}."
                )
            company_code = matches[0][1].get("companyCode")
            if not company_code:
                raise ValueError(f"No KAP companyCode is stored for {requested_symbol}.")
            selected_codes.add(str(company_code))

        company_links = [
            (url, label)
            for url, label in company_links
            if (match := re.search(r"/ozet/(\d+)(?:-|/|$)", url))
            and match.group(1) in selected_codes
        ]
        found_codes = {
            match.group(1)
            for url, _ in company_links
            if (match := re.search(r"/ozet/(\d+)(?:-|/|$)", url))
        }
        missing_codes = selected_codes - found_codes
        if missing_codes:
            raise ValueError(
                "Could not find the requested KAP company page(s) in the BIST list: "
                + ", ".join(sorted(missing_codes))
            )
        logger.info("Single-company mode selected %s KAP page(s)", len(company_links))

    if non_traded_cache:
        logger.info("Loaded %s cached non-share-traded members from PostgreSQL", len(non_traded_cache))

    if start_after:
        resume_token = start_after.strip().upper()
        for offset, (_, list_code) in enumerate(company_links):
            if list_code.upper() == resume_token:
                company_links = company_links[offset + 1:]
                logger.info("Resuming after %s; %s pages remain", start_after, len(company_links))
                break
        else:
            logger.warning("--start-after value was not found in discovered KAP list: %s", start_after)

    if missing_only:
        existing_symbols = {symbol.upper() for symbol in companies}
        existing_symbols.update(
            alias.upper()
            for info in companies.values()
            for alias in info.get("symbolAliases", [])
        )
        cached_non_traded_symbols = (
            set() if recheck_non_traded else {symbol.upper() for symbol in non_traded_cache}
        )
        known_symbols = existing_symbols | cached_non_traded_symbols
        original_count = len(company_links)
        company_links = [
            (summary_url, list_code)
            for summary_url, list_code in company_links
            if not normalize_list_symbols(list_code).issubset(known_symbols)
        ]
        logger.info(
            "Missing-only mode kept %s/%s KAP pages for detail fetching",
            len(company_links),
            original_count,
        )

    if limit:
        company_links = company_links[:limit]

    logger.info("Discovered %s KAP BIST company/member pages", len(company_links))

    skipped = 0
    last_updated = datetime.now(UTC).isoformat()
    for index, (summary_url, list_code) in enumerate(company_links, start=1):
        general_url = summary_url.replace("/ozet/", "/genel/")
        try:
            summary_html = fetch_html(
                session,
                summary_url,
                retries=retries,
                retry_backoff=retry_backoff,
                rate_limit_backoff=rate_limit_backoff,
            )
            general_html = fetch_html(
                session,
                general_url,
                retries=retries,
                retry_backoff=retry_backoff,
                rate_limit_backoff=rate_limit_backoff,
            )
            symbol, _info, is_traded, infos_by_symbol = build_company_info(
                summary_html,
                general_html,
                summary_url=summary_url,
                general_url=general_url,
                last_updated=last_updated,
            )
        except Exception:
            logger.exception("Failed to process %s (%s)", list_code, summary_url)
            if sleep_seconds:
                sleep(sleep_seconds)
            continue

        if not symbol:
            logger.warning("Skipping page without a stock code: %s", summary_url)
            skipped += 1
            continue

        if is_traded or include_non_traded:
            kept_symbols = sorted(infos_by_symbol)
            if not kept_symbols:
                logger.warning(
                    "Skipping %s: no unambiguous canonical traded symbol was found",
                    list_code,
                )
                skipped += 1
                continue

            try:
                for kept_symbol in kept_symbols:
                    updated_info = copy.deepcopy(infos_by_symbol[kept_symbol])
                    oid = updated_info.get("mkkMemberOid")
                    old_keys = {
                        existing_symbol
                        for existing_symbol, existing_info in companies.items()
                        if oid and existing_info.get("mkkMemberOid") == oid
                    }
                    updated_info["symbolAliases"] = sorted(
                        (
                            set(updated_info.get("symbolAliases") or [])
                            | old_keys
                            | {
                                alias
                                for old_key in old_keys
                                for alias in (companies[old_key].get("symbolAliases") or [])
                            }
                        ) - {kept_symbol}
                    )
                    upsert_company_metadata(conn, kept_symbol, updated_info)
                    delete_non_traded_codes(
                        conn,
                        [kept_symbol, *updated_info.get("symbolAliases", []), *normalize_list_symbols(list_code)],
                    )
                    conn.commit()
                    for old_key in old_keys:
                        companies.pop(old_key, None)
                    companies[kept_symbol] = updated_info
            except Exception:
                conn.rollback()
                logger.exception("Failed to save company metadata for %s", list_code)
                if sleep_seconds:
                    sleep(sleep_seconds)
                continue
            logger.info("[%s/%s] kept %s", index, len(company_links), ", ".join(kept_symbols))
        else:
            skipped += 1
            try:
                for cached_symbol in normalize_list_symbols(list_code):
                    record = {
                        "kapSummaryUrl": summary_url,
                        "kapGeneralUrl": general_url,
                        "lastChecked": last_updated,
                    }
                    upsert_non_traded_metadata(conn, cached_symbol, record)
                    non_traded_cache[cached_symbol] = record
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception("Failed to save non-traded metadata for %s", list_code)
                if sleep_seconds:
                    sleep(sleep_seconds)
                continue
            logger.info("[%s/%s] skipped non-share-traded member %s", index, len(company_links), list_code)

        if sleep_seconds:
            sleep(sleep_seconds)

    logger.info("Stored %s company records in bist_finance; skipped %s", len(companies), skipped)
    logger.info("Stored %s cached non-share-traded members in bist_finance", len(non_traded_cache))
    conn.close()
    return companies


def main():
    configure_logging()
    args = parse_args()
    refresh_company_info(
        limit=args.limit,
        sleep_seconds=args.sleep,
        include_non_traded=args.include_non_traded,
        retries=args.retries,
        retry_backoff=args.retry_backoff,
        rate_limit_backoff=args.rate_limit_backoff,
        missing_only=args.missing_only,
        recheck_non_traded=args.recheck_non_traded,
        start_after=args.start_after,
        symbols=args.symbols,
    )


if __name__ == "__main__":
    main()
