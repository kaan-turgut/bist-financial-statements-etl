import json

from models.company import Company
from paths import DATA_MAPPINGS_DIR


def load_companies(json_path=DATA_MAPPINGS_DIR / "company_info.json"):
    with open(json_path, encoding="utf-8") as f:
        company_info = json.load(f)

    companies = {}
    seen_oids = {}

    for symbol, data in company_info.items():
        oid = data.get("mkkMemberOid")
        if oid in seen_oids:
            raise ValueError(
                f"Duplicate MKK OID {oid} in company_info.json for "
                f"{seen_oids[oid]} and {symbol}. Consolidate the records first."
            )
        seen_oids[oid] = symbol
        companies[symbol] = Company.from_kap_data(symbol, data)

    return companies


def symbol_aliases(json_path=DATA_MAPPINGS_DIR / "company_info.json"):
    with open(json_path, encoding="utf-8") as f:
        company_info = json.load(f)

    aliases = {}
    canonical_symbols = set(company_info)
    for canonical, data in company_info.items():
        for alias in data.get("symbolAliases", []):
            alias = alias.strip().upper()
            if alias in canonical_symbols and alias != canonical:
                continue
            previous = aliases.get(alias)
            if previous and previous != canonical:
                raise ValueError(f"Symbol alias {alias} maps to multiple companies.")
            aliases[alias] = canonical
    return aliases


def resolve_symbols(symbols, json_path=DATA_MAPPINGS_DIR / "company_info.json"):
    companies = load_companies(json_path)
    aliases = symbol_aliases(json_path)
    resolved = []
    for symbol in symbols:
        normalized = symbol.strip().upper()
        canonical = normalized if normalized in companies else aliases.get(normalized)
        if canonical is None:
            raise ValueError(f"Unknown company symbol: {symbol}")
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved


def resolve_price_symbols(symbols, json_path=DATA_MAPPINGS_DIR / "company_info.json"):
    """Resolve issuer aliases while preserving independently traded share classes."""
    with open(json_path, encoding="utf-8") as f:
        company_info = json.load(f)

    aliases = symbol_aliases(json_path)
    traded_classes = {
        share_class.get("symbol", "").strip().upper()
        for data in company_info.values()
        for share_class in data.get("shareClasses", [])
        if share_class.get("symbol")
    }
    canonical_symbols = set(company_info)
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
