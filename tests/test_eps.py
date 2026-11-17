"""EPS accounting, source-unit, extraction and database regressions."""

import json
from datetime import date
from decimal import Decimal

import pytest

from models.earnings_per_share import (
    EpsObservation,
    calculate_history,
    normalize_observation,
)
from read_statements import eps as extractor
from read_statements.helpers import classify_texts
from services.earnings_per_share import (
    calculate_eps_changes,
    calculate_eps_ratios,
    import_eps_postgres,
    store_eps_source,
)


def observation(start, end, **kwargs):
    defaults = {"period_kind": "quarter", "attributable_profit": Decimal(100),
                "weighted_average_shares": Decimal(100), "share_basis_id": "verified-basis"}
    defaults.update(kwargs)
    return EpsObservation(period_start=date.fromisoformat(start), period_end=date.fromisoformat(end), **defaults)


def test_kurus_nominal_share_money_and_share_count_units_are_independent():
    o = normalize_observation(period_start=date(2024, 1, 1), period_end=date(2024, 12, 31), period_kind="ytd",
        reported_eps="9.51", attributable_profit="18315157", weighted_average_shares="192679559800",
        eps_unit="KURUS", money_multiplier=1000, share_nominal_value="0.01", rounding_tolerance="0.0051")
    assert o.value == Decimal("9.51")
    assert o.attributable_profit == 18315157000
    assert o.weighted_average_shares == 1926795598
    assert o.normalization["raw_reported_eps"] == "9.51"


def test_thousand_share_count_does_not_multiply_eps():
    o = normalize_observation(period_start=date(2024, 1, 1), period_end=date(2024, 12, 31), period_kind="ytd",
        reported_eps="31.15", attributable_profit="18586744", weighted_average_shares="596774",
        money_multiplier=1000, shares_multiplier=1000, rounding_tolerance="0.0051")
    assert o.value == Decimal("31.15")
    assert o.weighted_average_shares == 596774000


def test_split_adjustment_changes_denominator_and_eps_not_earnings():
    o = normalize_observation(period_start=date(2024, 1, 1), period_end=date(2024, 3, 31), period_kind="quarter",
        reported_eps="2", attributable_profit=200, weighted_average_shares=100,
        split_adjustment_factor=2, share_basis_id="after-two-for-one")
    assert o.value == 1
    assert o.attributable_profit == 200
    assert o.weighted_average_shares == 200


@pytest.mark.parametrize("values", [
    {"weighted_average_shares": 0}, {"weighted_average_shares": -1},
    {"reported_eps": "NaN"}, {"reported_eps": "Infinity"},
    {"reported_eps": "9", "attributable_profit": 100, "weighted_average_shares": 100},
])
def test_invalid_or_unreconciled_inputs_are_rejected(values):
    with pytest.raises(ValueError):
        observation("2024-01-01", "2024-03-31", **values)


def test_quarter_from_ytd_uses_day_weighted_shares_not_eps_subtraction():
    q1 = observation("2024-01-01", "2024-03-31", period_kind="ytd")
    q2 = observation("2024-01-01", "2024-06-30", period_kind="ytd",
                     attributable_profit=300, weighted_average_shares=150)
    result = calculate_history({(2024, 1): [q1], (2024, 2): [q2]})
    assert result[(2024, 2, "basic", "ordinary", "total")]["value_quarter"] == 1
    assert q2.value-q1.value == 1  # Deliberately check the denominator separately below.
    q2 = observation("2024-01-01", "2024-06-30", period_kind="ytd",
                     attributable_profit=500, weighted_average_shares=150)
    result = calculate_history({(2024, 1): [q1], (2024, 2): [q2]})
    assert result[(2024, 2, "basic", "ordinary", "total")]["value_quarter"] == 2
    assert q2.value-q1.value != 2


def test_q4_is_not_annual_eps_minus_nine_month_eps():
    q3 = observation("2024-01-01", "2024-09-30", period_kind="ytd",
                     attributable_profit=300, weighted_average_shares=100)
    annual = observation("2024-01-01", "2024-12-31", period_kind="ytd",
                         attributable_profit=600, weighted_average_shares=150)
    result = calculate_history({(2024, 3): [q3], (2024, 4): [annual]})[(2024, 4, "basic", "ordinary", "total")]
    expected_shares = (Decimal(150)*366-Decimal(100)*274)/92
    assert result["value_quarter"] == Decimal(300)/expected_shares
    assert result["value_ttm"] == 4


def test_ttm_uses_period_earnings_and_day_weighted_shares():
    periods = ((2023, 2, "2023-04-01", "2023-06-30", 100),
               (2023, 3, "2023-07-01", "2023-09-30", 100),
               (2023, 4, "2023-10-01", "2023-12-31", 200),
               (2024, 1, "2024-01-01", "2024-03-31", 200))
    history = {(y, q): [observation(start, end, weighted_average_shares=shares)]
               for y, q, start, end, shares in periods}
    result = calculate_history(history)[(2024, 1, "basic", "ordinary", "total")]
    assert result["value_ttm"] == Decimal(400)/150
    assert result["value_ttm"] != sum(o[0].value for o in history.values())


@pytest.mark.parametrize("problem", ["gap", "unknown_basis", "split_basis", "purchasing_power", "missing_shares"])
def test_ttm_rejects_missing_or_incompatible_inputs(problem):
    history = {(2023, 2): [observation("2023-04-01", "2023-06-30")],
               (2023, 3): [observation("2023-07-01", "2023-09-30")],
               (2023, 4): [observation("2023-10-01", "2023-12-31")],
               (2024, 1): [observation("2024-01-01", "2024-03-31")]}
    if problem == "gap":
        del history[(2023, 3)]
    else:
        fields = {"unknown_basis": {"share_basis_id": None}, "split_basis": {"share_basis_id": "another-basis"},
                  "purchasing_power": {"purchasing_power_date": date(2023, 9, 30)},
                  "missing_shares": {"reported_eps": 1, "weighted_average_shares": None}}[problem]
        history[(2023, 3)] = [observation("2023-07-01", "2023-09-30", **fields)]
    assert calculate_history(history)[(2024, 1, "basic", "ordinary", "total")]["value_ttm"] is None


def test_reported_eps_is_preferred_and_basic_diluted_stay_distinct():
    basic = observation("2024-01-01", "2024-03-31", reported_eps="1.00")
    diluted = observation("2024-01-01", "2024-03-31", eps_kind="diluted",
                          reported_eps="0.80", weighted_average_shares=125)
    result = calculate_history({(2024, 1): [basic, diluted]})
    assert result[(2024, 1, "basic", "ordinary", "total")]["value_quarter"] == 1
    assert result[(2024, 1, "diluted", "ordinary", "total")]["value_quarter"] == Decimal("0.8")


def test_decimal_statement_values_are_not_truncated_or_rejected():
    assert classify_texts(["1.234,56"])[0][1] == Decimal("1234.56")


NOTE_TEXT = """KONSOLİDE FİNANSAL TABLOLARA İLİŞKİN DİPNOTLAR
(Tutarlar aksi belirtilmedikçe bin Türk Lirası olarak ifade edilmiştir.)
28. Pay başına kazanç
                                             1 Ocak -                 1 Ocak -
                                       31 Aralık 2024            31 Aralık 2023
Ana ortaklığa ait dönem karı                 18.315.157                77.354.421
Beheri 1 kr. nominal değerli
hisse senetlerinin ağırlıklı ortalama adedi 192.679.559.800           192.679.559.800
Nominal değeri 1 kr. olan
pay başına kazanç (kr.)                           9,51                     40,15
29. İlişkili taraf açıklamaları
"""


def test_pdf_note_extracts_disclosed_denominator_profit_and_original_units(monkeypatch):
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [NOTE_TEXT])
    values, _ = extractor.extract_pdf_eps(b"fixture", 2024, 4)
    assert len(values) == 1
    assert values[0].value == Decimal("9.51")
    assert values[0].attributable_profit == 18315157000
    assert values[0].weighted_average_shares == 1926795598
    assert values[0].share_basis_id is None  # Do not invent a cross-filing split basis.


def test_eps_fiscal_year_label_need_not_equal_the_period_end_year(monkeypatch):
    text=NOTE_TEXT.replace('1 Ocak -','1 Nisan -').replace('31 Aralık 2024','31 Mart 2025').replace('31 Aralık 2023','31 Mart 2024')
    monkeypatch.setattr(extractor,'pdf_pages',lambda _:[text])
    values,_=extractor.extract_pdf_eps(b'fixture',2024,4)
    assert values[0].period_start==date(2024,4,1)
    assert values[0].period_end==date(2025,3,31)


def test_pdf_parenthesized_losses_preserve_the_sign():
    assert extractor.parse_number("( 1,17)") == Decimal("-1.17")
    assert extractor._values("Dönem karı / (zararı)     ( 100)     200", 2) == [Decimal(-100), Decimal(200)]


@pytest.mark.parametrize("token", ["1.234.56", "1,,23", "1,23.", "12...", "(1,2,3)", "1)", "NaN", "Infinity"])
def test_malformed_eps_numbers_are_rejected_without_decimal_errors(token):
    with pytest.raises(ValueError, match="Invalid EPS number"):
        extractor.parse_number(token)


@pytest.mark.parametrize("token,expected", [
    ("1.234.567,89", "1234567.89"), ("1.234.567", "1234567"),
    ("1,17", "1.17"), ("1.17", "1.17"), ("( 1.234,56)", "-1234.56"),
    ("-0,23", "-0.23"), ("–", None),
])
def test_eps_number_validation_preserves_supported_formats(token, expected):
    assert extractor.parse_number(token) == (Decimal(expected) if expected is not None else None)


def test_malformed_face_row_is_reported_and_valid_eps_note_still_extracts(monkeypatch):
    bad_page = "Pay başına kazanç (TL)     1,2,3     4,56"
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [bad_page, NOTE_TEXT])
    values, payload = extractor.extract_pdf_eps(b"fixture", 2024, 4)
    assert len(values) == 1
    assert values[0].value == Decimal("9.51")
    assert any("face page 1: Invalid EPS number" in warning
               for warning in payload["extraction_warnings"])


def test_malformed_eps_cell_does_not_shift_or_partially_accept_columns(monkeypatch):
    bad_page = """Pay başına kazanç (TL)     1.234.56     4,56
Pay başına kar (TL)       2,34        1,,23
"""
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [bad_page])
    values, payload = extractor.extract_pdf_eps(b"fixture", 2024, 4)
    assert values == []
    assert len(payload["extraction_warnings"]) == 2


def test_note_with_quarter_and_ytd_columns_keeps_them_distinct(monkeypatch):
    text = """KONSOLİDE DİPNOTLAR
(Tutarlar Milyon Türk Lirası (TL) olarak gösterilmiştir.)
27. PAY BAŞINA KAZANÇ
                    1 Ocak-         1 Nisan-        1 Ocak-         1 Nisan-
                30 Haziran 2018  30 Haziran 2018 30 Haziran 2017 30 Haziran 2017
Payların dönem içerisindeki
ağırlıklı ortalaması (tam adet) 138.000.000.000 138.000.000.000 138.000.000.000 138.000.000.000
Dönem karı / (zararı)                      127             441         ( 1.615)          ( 194)
Pay başına kazanç / (kayıp) (Kr)           0,09            0,32          ( 1,17)          ( 0,14)
"""
    capital = "20. ÖZKAYNAKLAR\nSermaye 1 Kr nominal değerli hisselerden oluşmaktadır."
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [capital, text])
    values, _ = extractor.extract_pdf_eps(b"fixture", 2018, 2)
    result = {o.period_kind: o for o in values}
    assert result["ytd"].value == Decimal("0.09")
    assert result["quarter"].value == Decimal("0.32")
    assert result["quarter"].attributable_profit == 441000000


def test_wrapped_date_headers_and_share_labels(monkeypatch):
    text = NOTE_TEXT.replace("                                             1 Ocak -                 1 Ocak -\n                                       31 Aralık 2024            31 Aralık 2023",
        "                  1 Ocak -31 Aralık           1 Ocak -31 Aralık\n                              2024                        2023")
    text = text.replace("hisse senetlerinin ağırlıklı ortalama adedi", "hisse senetlerinin ağırlıklı\n  ortalama adedi")
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [text])
    values, _ = extractor.extract_pdf_eps(b"fixture", 2024, 4)
    assert values[0].value == Decimal("9.51")
    assert values[0].period_end == date(2024, 12, 31)


def test_missing_q1_observation_or_eps_kind_is_unknown_not_an_error():
    history = {(2024, 1): [], (2024, 2): [observation("2024-04-01", "2024-06-30")]}
    result = calculate_history(history)
    assert result[(2024, 1, "basic", "ordinary", "total")]["value_quarter"] is None


def test_pdf_with_conflicting_calculation_is_quarantined(monkeypatch):
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [NOTE_TEXT.replace("9,51", "99,51")])
    with pytest.raises(ValueError, match="reconciliation"):
        extractor.extract_pdf_eps(b"fixture", 2024, 4)


def test_foreign_currency_earnings_are_not_interpreted_as_try(monkeypatch):
    text = NOTE_TEXT.replace("bin Türk Lirası", "bin ABD Doları")
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [text])
    values, payload = extractor.extract_pdf_eps(b"fixture", 2024, 4)
    assert all(not value.has_inputs for value in values)
    assert any("TRY" in warning for warning in payload["extraction_warnings"])


def test_million_share_counts_are_scaled_independently(monkeypatch):
    text = NOTE_TEXT.replace("ortalama adedi", "ortalama adedi (milyon)").replace("192.679.559.800", "192.679,5598")
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [text])
    values, _ = extractor.extract_pdf_eps(b"fixture", 2024, 4)
    assert values[0].weighted_average_shares == 1926795598


def test_unknown_nominal_share_unit_is_not_guessed(monkeypatch):
    text = NOTE_TEXT.replace("1 kr. nominal değerli", "bilinmeyen nominal değerli").replace("Nominal değeri 1 kr. olan", "")
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [text])
    values, payload = extractor.extract_pdf_eps(b"fixture", 2024, 4)
    assert values == []
    assert payload["extraction_warnings"]


def test_face_statement_maps_ytd_and_quarter_columns_by_their_dates(monkeypatch):
    capital = "17. Özkaynaklar\nŞirket sermayesi 1 kr. nominal değerli hisselerden oluşmaktadır."
    face = """KONSOLİDE KAR VEYA ZARAR TABLOSU
                  1 Ocak -       1 Nisan -       1 Ocak -       1 Nisan -
              30 Haziran 2024 30 Haziran 2024 30 Haziran 2023 30 Haziran 2023
Pay Başına Kar (Kr)       26       27,04          22,03           13,17           9,97
Sulandırılmış Pay Başına Kar (Kr) 26 27,04         22,03           13,17           9,97
"""
    monkeypatch.setattr(extractor, "pdf_pages", lambda _: [capital, face])
    values, _ = extractor.extract_pdf_eps(b"fixture", 2024, 2)
    result = {(o.period_kind, o.eps_kind): o for o in values}
    assert result[("ytd", "basic")].value == Decimal("27.04")
    assert result[("quarter", "basic")].value == Decimal("22.03")
    assert result[("quarter", "basic")].period_start == date(2024, 4, 1)
    assert result[("quarter", "diluted")].value == Decimal("22.03")


def test_nonmonetary_eps_rows_are_not_scaled_into_financial_values():
    import pandas as pd

    frame = pd.DataFrame([{
        "company": "TEST", "year": 2024, "quarter": 1, "date": "01.01.2024 - 31.03.2024",
        "table": "Income Statement", "item": "pay başına kazanç", "line_item_path": "pay başına kazanç",
        "value": Decimal("1.25"), "reporting unit": 1000000,
    }])
    from services.postgres_financial_pipeline import _clean_frame

    assert _clean_frame(frame).empty


def seed_gold(database):
    conn, company, run = database
    conn.execute("""INSERT INTO gold.financial_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,statement_type,line_item_path,item,value_quarter)
        VALUES (%s,%s,2024,4,'income_statement','profit or loss > dönem karı (zararı)','dönem karı (zararı)',999999)""", (run, company))
    return conn, company, run


def test_gold_does_not_use_current_shares_or_group_profit(database):
    conn, _, run = seed_gold(database)
    calculate_eps_ratios(conn, run)
    values = conn.execute("SELECT value_quarter,value_ytd,value_ttm FROM gold.financial_ratios WHERE ratio_code='eps'").fetchone()
    assert all(v is None for v in values.values())


def test_verified_reported_source_overrides_proxy_and_retains_provenance(database):
    conn, company, run = seed_gold(database)
    o = observation("2024-01-01", "2024-12-31", period_kind="ytd", attributable_profit=1200, weighted_average_shares=100)
    store_eps_source(conn, company_id=company, year=2024, quarter=4, source_path="verified.json", source_hash="test",
                     source_kind="json", payload={}, observations=[o], run_id=run)
    calculate_eps_ratios(conn, run)
    row = conn.execute("SELECT value_quarter,value_ytd,value_ttm FROM gold.financial_ratios WHERE ratio_code='eps'").fetchone()
    assert row["value_quarter"] is None
    assert row["value_ytd"] == row["value_ttm"] == 12
    detail = conn.execute("SELECT calculation_details FROM gold.eps_values WHERE eps_kind='basic' AND earnings_scope='total'").fetchone()["calculation_details"]
    assert detail["ttm_method"] == "annual_report"
    assert detail["source_ids"]


def test_json_import_is_atomic_for_invalid_inputs(database, tmp_path):
    conn, _, _ = database
    file = tmp_path / "eps.json"
    file.write_text(json.dumps({"observations": [{"symbol": "TEST", "fiscal_year": 2024, "fiscal_quarter": 1,
        "period_start": "2024-01-01", "period_end": "2024-03-31", "period_kind": "quarter",
        "reported_eps": "99", "attributable_profit": "100", "weighted_average_shares": "100"}]}))
    with pytest.raises(ValueError, match="reconcile"):
        import_eps_postgres(file)
    assert conn.execute("SELECT COUNT(*) AS n FROM bronze.eps_sources").fetchone()["n"] == 0


def test_source_rejects_duplicate_series_even_with_different_dates(database):
    conn, company, run = database
    observations = [observation("2024-07-01", "2024-09-30"), observation("2024-07-02", "2024-09-30")]
    with pytest.raises(ValueError, match="Duplicate"):
        store_eps_source(conn, company_id=company, year=2024, quarter=3, source_path="test.json",
                         source_hash="duplicate", source_kind="json", payload={}, observations=observations, run_id=run)
    assert conn.execute("SELECT COUNT(*) AS n FROM bronze.eps_sources").fetchone()["n"] == 0


@pytest.mark.parametrize("verified", [False, True])
def test_eps_growth_requires_verified_common_share_basis(database, verified):
    conn, company, run = seed_gold(database)
    conn.execute("""INSERT INTO gold.financial_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,statement_type,line_item_path,item)
        VALUES (%s,%s,2024,3,'income_statement','profit or loss > dönem karı (zararı)','dönem karı (zararı)')""", (run, company))
    for quarter, start, end, profit in ((3, "2024-07-01", "2024-09-30", 100), (4, "2024-10-01", "2024-12-31", 200)):
        o = observation(start, end, attributable_profit=profit, share_basis_id="verified" if verified else None)
        store_eps_source(conn, company_id=company, year=2024, quarter=quarter, source_path="test.json",
                         source_hash=str(quarter), source_kind="json", payload={}, observations=[o], run_id=run)
    calculate_eps_ratios(conn, run)
    calculate_eps_changes(conn, run)
    change = conn.execute("SELECT quarter_qoq_change FROM gold.financial_ratios WHERE ratio_code='eps' AND fiscal_quarter=4").fetchone()["quarter_qoq_change"]
    assert change == (1 if verified else None)


def test_missing_archive_member_fails_only_that_filing(tmp_path):
    import zipfile

    from services.earnings_per_share import _extract_local_filing

    archive = tmp_path / "TEST_2025.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("TEST_2_2025_2.xls", "x")
    result = _extract_local_filing({
        "source_path": f"{archive}/TEST_1_2025_1.xls",
        "fiscal_year": 2025, "fiscal_quarter": 1, "period_end": None,
    })
    assert isinstance(result, FileNotFoundError)
    assert "missing from its archive" in str(result)
