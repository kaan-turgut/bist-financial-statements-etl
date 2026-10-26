from services.company_loader import resolve_price_symbols, resolve_symbols
from services.update_company_info import (
    consolidate_company_info_by_oid,
    extract_capital_market_instrument,
    normalize_missing_values,
    parse_number,
    value_after,
)


def company(oid, symbol, *, shares=100):
    return {
        "mkkMemberOid": oid,
        "freeFloatSymbol": symbol,
        "shareClasses": [{
            "symbol": symbol,
            "freeFloatShares": shares,
            "freeFloatRatio": 20,
        }],
        "lastUpdated": "2026-10-01",
    }


def test_consolidation_uses_current_traded_symbol_and_keeps_legacy_alias():
    records = {
        "FIN": {
            "mkkMemberOid": "oid-1",
            "freeFloatSymbol": None,
            "symbolAliases": ["FIN"],
        },
        "QNBTR": company("oid-1", "QNBTR"),
    }

    result, removed = consolidate_company_info_by_oid(records)

    assert removed == 1
    assert list(result) == ["QNBTR"]
    assert "FIN" in result["QNBTR"]["symbolAliases"]
    assert result["QNBTR"]["freeFloatSymbol"] == "QNBTR"
    assert "primarySymbol" not in result["QNBTR"]
    assert "stockCode" not in result["QNBTR"]
    assert "freeFloatShares" not in result["QNBTR"]
    assert "freeFloatRatio" not in result["QNBTR"]


def test_consolidation_keeps_all_share_classes_but_one_company_key():
    records = {
        "KRDMA": company("oid-2", "KRDMA", shares=20),
        "KRDMB": company("oid-2", "KRDMB", shares=30),
        "KRDMD": company("oid-2", "KRDMD", shares=50),
    }

    result, removed = consolidate_company_info_by_oid(records)

    assert removed == 2
    assert list(result) == ["KRDMD"]
    assert {row["symbol"] for row in result["KRDMD"]["shareClasses"]} == {
        "KRDMA", "KRDMB", "KRDMD"
    }
    assert {"KRDMA", "KRDMB"}.issubset(result["KRDMD"]["symbolAliases"])
    assert "shareClass" not in result["KRDMD"]


def test_unverified_duplicate_oid_is_not_guessed_or_removed():
    records = {
        "OLD": {"mkkMemberOid": "oid-3", "freeFloatSymbol": None},
        "MAYBE": {"mkkMemberOid": "oid-3", "freeFloatSymbol": None},
    }

    result, removed = consolidate_company_info_by_oid(records)

    assert removed == 0
    assert result == records


def test_resolve_symbols_collapses_alias_and_canonical_to_one_symbol(tmp_path):
    path = tmp_path / "company_info.json"
    path.write_text(
        '{"QNBTR": {"mkkMemberOid": "oid-1", "symbolAliases": ["FIN"], '
        '"kapMemberTitle": "QNB", "Website": "", "cityName": "", '
        '"Sectors": [], "Indexes": [], "activityField": "", '
        '"paidInCapital": 1, "capitalCeiling": 1, "numSharesOutstanding": 1, '
        '"capitalDetails": {}}}',
        encoding="utf-8",
    )

    assert resolve_symbols(["FIN", "QNBTR"], path) == ["QNBTR"]


def test_price_symbol_resolution_preserves_distinct_share_classes(tmp_path):
    path = tmp_path / "company_info.json"
    path.write_text(
        '{"ISCTR": {"mkkMemberOid": "oid-1", "symbolAliases": ["ISATR"], '
        '"shareClasses": [{"symbol": "ISATR"}, {"symbol": "ISCTR"}], '
        '"kapMemberTitle": "IS", "Website": "", "cityName": "", '
        '"Sectors": [], "Indexes": [], "activityField": "", '
        '"paidInCapital": 1, "capitalCeiling": 1, "numSharesOutstanding": 1, '
        '"capitalDetails": {}}}',
        encoding="utf-8",
    )

    assert resolve_price_symbols(["ISATR", "ISCTR"], path) == ["ISATR", "ISCTR"]


def test_market_instrument_parser_preserves_blank_listing_date_cell():
    html = """
    <table>
      <tr><td>PAY SENEDİ</td><td></td><td>TÜRKİYE</td>
          <td>BORSA İSTANBUL A.Ş</td><td>ANA PAZAR</td></tr>
    </table>
    """

    result = extract_capital_market_instrument(
        ["Sermaye Piyasası Aracının İşlem Gördüğü Pazar", "ANA PAZAR", "PAY SENEDİ"],
        html,
    )

    assert result == {
        "type": "PAY SENEDİ",
        "listingDate": None,
        "country": "TÜRKİYE",
        "exchange": "BORSA İSTANBUL A.Ş",
        "market": "ANA PAZAR",
    }


def test_market_instrument_parser_keeps_populated_listing_date():
    html = """
    <table>
      <tr><td>PAY SENEDİ</td><td>04.12.2020</td><td>TÜRKİYE</td>
          <td>BORSA İSTANBUL A.Ş</td><td>ANA PAZAR</td></tr>
    </table>
    """

    result = extract_capital_market_instrument(
        ["PAY SENEDİ", "04.12.2020", "TÜRKİYE", "BORSA İSTANBUL A.Ş", "ANA PAZAR"],
        html,
    )

    assert result["listingDate"] == "04.12.2020"
    assert result["country"] == "TÜRKİYE"
    assert result["exchange"] == "BORSA İSTANBUL A.Ş"
    assert result["market"] == "ANA PAZAR"


def test_flattened_market_row_fallback_handles_blank_listing_date():
    result = extract_capital_market_instrument(
        ["PAY SENEDİ", "TÜRKİYE", "BORSA İSTANBUL A.Ş", "ANA PAZAR"]
    )

    assert result["listingDate"] is None
    assert result["country"] == "TÜRKİYE"
    assert result["exchange"] == "BORSA İSTANBUL A.Ş"
    assert result["market"] == "ANA PAZAR"


def test_kap_missing_value_marker_becomes_none_in_values_and_numbers():
    marker = "Bilgi Mevcut Değil"

    assert value_after(["Kayıtlı Sermaye Tavanı", marker], "Kayıtlı Sermaye Tavanı") is None
    assert parse_number(marker) is None
    assert normalize_missing_values({"field": marker, "items": [marker, "valid"]}) == {
        "field": None,
        "items": [None, "valid"],
    }
