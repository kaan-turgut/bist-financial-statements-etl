"""Load and resolve company identifiers from PostgreSQL metadata."""

from models.company import Company
from services.metadata_store import connect_metadata_db, load_company_records


def _symbol_aliases(company_records):
    aliases = {}
    canonical_symbols = set(company_records)
    for canonical, data in company_records.items():
        for alias in data.get("symbolAliases") or []:
            alias = alias.strip().upper()
            if alias in canonical_symbols and alias != canonical:
                continue
            previous = aliases.get(alias)
            if previous and previous != canonical:
                raise ValueError(f"Symbol alias {alias} maps to multiple companies.")
            aliases[alias] = canonical
    return aliases


def _load_company_records():
    conn = connect_metadata_db()
    try:
        records = load_company_records(conn)
    finally:
        conn.close()
    seen_oids = {}
    for symbol, data in records.items():
        oid = data.get("mkkMemberOid")
        if oid in seen_oids:
            raise ValueError(
                f"Duplicate MKK OID {oid} in metadata.companies for "
                f"{seen_oids[oid]} and {symbol}."
            )
        seen_oids[oid] = symbol
    return records


def load_companies():
    return {
        symbol: Company.from_kap_data(symbol, data)
        for symbol, data in _load_company_records().items()
    }


def symbol_aliases():
    return _symbol_aliases(_load_company_records())


def resolve_symbols(symbols):
    records = _load_company_records()
    aliases = _symbol_aliases(records)
    resolved = []
    for symbol in symbols:
        normalized = symbol.strip().upper()
        canonical = normalized if normalized in records else aliases.get(normalized)
        if canonical is None:
            raise ValueError(f"Unknown company symbol: {symbol}")
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved


def resolve_price_symbols(symbols):
    """Resolve issuer aliases while preserving independently traded share classes."""
    records = _load_company_records()
    aliases = _symbol_aliases(records)
    traded_classes = {
        share_class.get("symbol", "").strip().upper()
        for data in records.values()
        for share_class in data.get("shareClasses") or []
        if share_class.get("symbol")
    }
    canonical_symbols = set(records)
    resolved = []
    for symbol in symbols:
        normalized = symbol.strip().upper()
        if normalized in traded_classes or normalized in canonical_symbols:
            canonical = normalized
        else:
            canonical = aliases.get(normalized)
        if canonical is None:
            raise ValueError(f"Unknown company or share-class symbol: {symbol}")
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved
