"""Dated HTML EPS, typed children, PDF unit evidence and source precedence."""

from datetime import date
from decimal import Decimal

import pytest

from models.earnings_per_share import normalize_observation
from read_statements.eps import extract_pdf_eps
from read_statements import eps as pdf_parser
from read_statements.structured_eps import (
    structured_eps_cells,
    normalize_structured_cells,
    merge_eps_observations,
)
from services.earnings_per_share import store_eps_source, update_eps_postgres


HEADERS = [
    "01.01.2026 - 30.06.2026",
    "01.01.2025 - 30.06.2025",
    "01.04.2026 - 30.06.2026",
    "01.04.2025 - 30.06.2025",
]


def html_report(
    values,
    *,
    typed=True,
    headers=None,
    label="Sürdürülen Faaliyetlerden Pay Başına Kazanç (Zarar)",
):
    headers = HEADERS if headers is None else headers
    heading = (
        "<tr>"
        + "".join(f'<td class="context-header">{h}</td>' for h in headers)
        + "</tr>"
    )
    abstract = "".join(
        '<tr class="abstract-row"><td>Pay Başına Kazanç</td></tr>' for _ in range(3)
    )
    empty = "".join('<td class="taxonomy-context-value"></td>' for _ in headers)
    parent = f'<tr class="general_role_310003-row-66 data-input-row"><td class="taxonomy-field-title">{label}</td>{empty}</tr>'
    numbers = "".join(
        f'<td class="taxonomy-context-value"><div>{v}</div></td>' for v in values
    )
    if typed:
        body = (
            parent
            + f'<tr class="new-type-row"><td></td><td></td><td><div class="typed-dimension-field-caption">{label}</div></td><td class="taxonomy-footnote-cell">19</td>{numbers}</tr>'
        )
    else:
        body = f'<tr class="general_role_310003-row-66 data-input-row"><td class="taxonomy-field-title">{label}</td><td class="taxonomy-footnote-cell">19</td>{numbers}</tr>'
    return f'<html><table class="financial-table">{heading}{abstract}{body}</table></html>'.encode()


@pytest.mark.parametrize("typed", [False, True])
def test_date_columns_determine_current_quarter_and_ytd_despite_label_offset(typed):
    cells, warnings = structured_eps_cells(
        html_report(["316,65", "185,55", "186,65", "115,69"], typed=typed), 2
    )
    assert warnings == []
    assert [
        (c["period_kind"], c["raw_value"], c["source_column_index"]) for c in cells
    ] == [("ytd", Decimal("316.65"), 1), ("quarter", Decimal("186.65"), 3)]
    assert all(c["period_end"] == date(2026, 6, 30) for c in cells)
    assert all(c["earnings_scope"] == "continuing" for c in cells)


def test_blank_numeric_cells_preserve_column_alignment_and_zero_is_not_missing():
    cells, _ = structured_eps_cells(html_report(["", "999", "0", "888"]), 2)
    assert len(cells) == 1
    assert cells[0]["raw_value"] == 0
    assert cells[0]["source_column_index"] == 3


def test_missing_date_header_does_not_shift_values():
    cells, _ = structured_eps_cells(
        html_report(
            ["1", "2", "3", "4"], headers=[HEADERS[0], "", HEADERS[2], HEADERS[3]]
        ),
        2,
    )
    assert [c["raw_value"] for c in cells] == [1, 3]


def test_column_count_mismatch_is_rejected():
    cells, warnings = structured_eps_cells(html_report(["1", "2", "3"]), 2)
    assert cells == []
    assert any("column count" in warning for warning in warnings)


def test_generic_child_caption_inherits_parent_scope_and_dilution():
    parent = "Sürdürülen Faaliyetlerden Sulandırılmış Pay Başına Kazanç (Zarar)"
    data = html_report(["1", "2", "3", "4"], label=parent)
    data = data.replace(
        f'typed-dimension-field-caption">{parent}'.encode(),
        'typed-dimension-field-caption">Pay Başına Kazanç (Tam TL)'.encode(),
    )
    cells, warnings = structured_eps_cells(data, 2)
    assert warnings == []
    assert len(cells) == 2
    assert {c["earnings_scope"] for c in cells} == {"continuing"}
    assert {c["eps_kind"] for c in cells} == {"diluted"}


def test_noncalendar_fiscal_period_uses_actual_dates():
    cells, _ = structured_eps_cells(
        html_report(
            ["1", "2", "3", "4"],
            headers=[
                "01.04.2026 - 30.09.2026",
                "01.04.2025 - 30.09.2025",
                "01.07.2026 - 30.09.2026",
                "01.07.2025 - 30.09.2025",
            ],
        ),
        2,
    )
    assert cells[0]["period_start"] == date(2026, 4, 1)
    assert cells[1]["period_start"] == date(2026, 7, 1)


def test_unknown_units_remain_unpublished_instead_of_using_statement_multiplier():
    cells, _ = structured_eps_cells(
        html_report(["316,65", "185,55", "186,65", "115,69"]), 2
    )
    observations, warnings = normalize_structured_cells(cells, [])
    assert observations == []
    assert warnings


def reference():
    return normalize_observation(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 6, 30),
        period_kind="ytd",
        reported_eps="316.65",
        eps_unit="KURUS",
        share_nominal_value=1,
        rounding_tolerance="0.01",
    )


def test_pdf_verified_unit_anchor_applies_to_both_columns_without_subtracting_eps():
    cells, _ = structured_eps_cells(
        html_report(["316,65", "185,55", "186,65", "115,69"]), 2
    )
    observations, warnings = normalize_structured_cells(cells, [reference()])
    assert warnings == []
    assert [o.value for o in observations] == [Decimal("3.1665"), Decimal("1.8665")]
    assert observations[1].period_start == date(2026, 4, 1)
    assert observations[1].normalization["source_column_index"] == 3


def test_conflicting_structured_quarter_cannot_override_verified_note():
    cells, _ = structured_eps_cells(
        html_report(["316,65", "185,55", "199,99", "115,69"]), 2
    )
    structured, _ = normalize_structured_cells(cells, [reference()])
    quarter = normalize_observation(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        period_kind="quarter",
        reported_eps="186.65",
        eps_unit="KURUS",
        earnings_scope="continuing",
    )
    with pytest.raises(ValueError, match="disagree"):
        merge_eps_observations([quarter, *structured])


def test_eps_note_with_four_wrapped_years_and_ordinary_share_label(monkeypatch):
    text = """KONSOLİDE DİPNOTLAR
(Tutarlar Bin Türk Lirası olarak ifade edilmiştir.)
19. PAY BAŞINA KAZANÇ
Pay başına kazanç ağırlıklı ortalama hisse adedine bölünmesiyle hesaplanır.
                     1 Ocak-      1 Nisan-     1 Ocak-     1 Nisan-
                  30 Haziran    30 Haziran   30 Haziran   30 Haziran
                        2026         2026         2025         2025
Adi hisse senetleri (Bin adet) 4.560.000 4.560.000 4.560.000 4.560.000
Net kâr - TL                 14.439.306 8.511.467 8.461.244 5.275.482
100 adet pay başına kazanç (tam kuruş) 316,65 186,65 185,55 115,69
"""
    capital = "12. Sermaye\nHer biri 1 TL itibari değerde paya bölünmüştür."
    monkeypatch.setattr(pdf_parser, "pdf_pages", lambda _: [capital, text])
    observations, _ = extract_pdf_eps(b"fixture", 2026, 2)
    assert len(observations) == 2
    assert observations[0].value == Decimal("3.1665")
    assert observations[1].value == Decimal("1.8665")
    assert observations[1].weighted_average_shares == 4560000000
    assert observations[1].attributable_profit == 8511467000


def test_verified_json_eps_source_is_preserved_during_automatic_ingestion(
    database, monkeypatch
):
    from services import earnings_per_share as service

    conn, company, run = database
    filing = conn.execute(
        """INSERT INTO bronze.statement_filings
        (company_id,fiscal_year,fiscal_quarter,statement_type,source_filename,source_path,source_sha256,run_id)
        VALUES (%s,2024,1,'income_statement','test.xls','missing.zip/test.xls','test',%s) RETURNING filing_key""",
        (company, run),
    ).fetchone()["filing_key"]
    conn.execute(
        """INSERT INTO silver.financial_observations
        (company_id,filing_key,processing_run_id,statement_type,fiscal_year,fiscal_quarter,period_start,period_end,line_item_path,item,amount)
        VALUES (%s,%s,%s,'income_statement',2024,1,'2024-01-01','2024-03-31','sales','sales',100)""",
        (company, filing, run),
    )
    conn.execute(
        """INSERT INTO ops.active_filing_versions
        (company_id,statement_type,fiscal_year,fiscal_quarter,filing_key,processing_run_id)
        VALUES (%s,'income_statement',2024,1,%s,%s)""",
        (company, filing, run),
    )
    obs = normalize_observation(
        period_start=date(2024, 1, 1),
        period_end=date(2024, 3, 31),
        period_kind="ytd",
        reported_eps=1,
        share_basis_id="verified-historical-basis",
    )
    store_eps_source(
        conn,
        company_id=company,
        year=2024,
        quarter=1,
        source_path="verified.json",
        source_hash="json",
        source_kind="json",
        payload={},
        observations=[obs],
        run_id=run,
    )
    before = conn.execute("SELECT * FROM ops.active_eps_sources").fetchone()
    conn.commit()
    monkeypatch.setattr(
        service,
        "extract_filing_eps",
        lambda *args: pytest.fail("Verified JSON must not be replaced"),
    )
    result = update_eps_postgres(companies=None, rebuild_gold=False)
    # The default published-only filter excludes unpublished companies.
    from types import SimpleNamespace

    result = update_eps_postgres([SimpleNamespace(symbol="TEST")], rebuild_gold=False)
    assert result["preserved_verified"] == 1
    assert conn.execute("SELECT * FROM ops.active_eps_sources").fetchone() == before


def test_wrapped_fiscal_dates_and_implicitly_scaled_disclosed_share_count(monkeypatch):
    text = """KONSOLİDE DİPNOTLAR
(Tutarlar Bin Türk Lirası olarak ifade edilmiştir.)
24   Pay başına kazanç
Pay başına kazanç ağırlıklı ortalama pay adedine bölünmesiyle hesaplanır.
                                   1 Şubat – 31      1 Mayıs –31      1 Şubat – 31      1 Mayıs – 31
                                  Temmuz 2026       Temmuz 2026       Temmuz 2025       Temmuz 2025
Ana ortaklık paylarına atfedilen net
 dönem karı                            980.256           263.007         1.463.317           304.435
Ağırlıklı ortalama adi hisse miktarı    794.512           794.512           794.512           794.512
Adi/seyreltilmiş pay başına
 kazanç (tam TL)                        1,2338            0,3310            1,8417            0,3832
"""
    capital = "20. Sermaye\nHer biri 1 TL itibari değerde paya bölünmüştür."
    monkeypatch.setattr(pdf_parser, "pdf_pages", lambda _: [capital, text])
    observations, _ = extract_pdf_eps(b"fixture", 2026, 2)
    assert [
        (o.period_start, o.period_end, o.value)
        for o in observations
        if o.eps_kind == "basic"
    ] == [
        (date(2026, 2, 1), date(2026, 7, 31), Decimal("1.2338")),
        (date(2026, 5, 1), date(2026, 7, 31), Decimal("0.3310")),
    ]
    assert all(o.weighted_average_shares == 794512000 for o in observations)
    assert len(observations) == 4
    assert {o.eps_kind for o in observations} == {"basic", "diluted"}


def test_note_keeps_explicit_continuing_scope(monkeypatch):
    from tests.test_eps import NOTE_TEXT

    text = NOTE_TEXT.replace(
        "pay başına kazanç (kr.)", "Sürdürülen faaliyetlerden pay başına kazanç (kr.)"
    )
    monkeypatch.setattr(pdf_parser, "pdf_pages", lambda _: [text])
    observations, _ = extract_pdf_eps(b"fixture", 2024, 4)
    assert observations
    assert {o.earnings_scope for o in observations} == {"continuing"}


def test_zero_nominal_share_value_is_rejected_before_division():
    with pytest.raises(ValueError, match="must be positive"):
        pdf_parser._nominal_value(
            [], "Sermaye 0 TL nominal değerli paylardan oluşmaktadır."
        )


def test_spawned_extraction_returns_rejected_sources_without_database_access(tmp_path):
    import zipfile
    from services.earnings_per_share import _prepared_eps_filings

    archive = tmp_path / "TEST_2026.zip"
    member = "TEST_123_2026_2.xls"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(member, html_report(["1", "2", "3", "4"]))
    filings = [
        dict(
            source_path=f"{archive}/{member}",
            fiscal_year=2026,
            fiscal_quarter=2,
            period_end=date(2026, 6, 30),
        )
        for _ in range(6)
    ]
    results = list(_prepared_eps_filings(filings, 2))
    assert len(results) == 6
    assert all(isinstance(result, ValueError) for _, result in results)


def test_legacy_typed_classification_is_corrected_from_same_source_cell():
    from types import SimpleNamespace
    from services.earnings_per_share import preserve_verified_eps

    old = normalize_observation(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        period_kind="quarter",
        reported_eps=1,
        normalization={
            "validation": "structured_dates_with_reconciled_pdf_table",
            "source_row_id": "310003:69",
            "source_column_index": 3,
        },
    )
    current = normalize_observation(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        period_kind="quarter",
        reported_eps=1,
        eps_kind="diluted",
        earnings_scope="continuing",
        normalization={
            "validation": "structured_dates_with_reconciled_pdf_table",
            "source_row_id": "310003:69",
            "source_column_index": 3,
            "parent_label": "Sürdürülen Faaliyetlerden Sulandırılmış Pay Başına Kazanç",
        },
    )
    conn = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(
            fetchall=lambda: [{**vars(old), "source_id": 77}]
        )
    )
    filing = dict(company_id=1, fiscal_year=2026, fiscal_quarter=2, filing_key=1)
    payload = {}
    observations = preserve_verified_eps(conn, filing, [current], payload)
    assert observations == [current]
    assert payload["corrected_legacy_classifications"][0]["from"] == ["basic", "total"]
    assert payload["corrected_legacy_classifications"][0]["to"] == [
        "diluted",
        "continuing",
    ]


def test_independent_reported_total_eps_is_preserved_when_continuing_eps_is_added():
    from types import SimpleNamespace
    from services.earnings_per_share import preserve_verified_eps

    old = normalize_observation(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        period_kind="quarter",
        reported_eps=1,
        normalization={"validation": "reported_face", "source_page": 4},
    )
    current = normalize_observation(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        period_kind="quarter",
        reported_eps=1,
        earnings_scope="continuing",
        normalization={
            "validation": "reconciled_note",
            "source_page": 4,
            "source_eps_label": "Sürdürülen faaliyetlerden pay başına kazanç",
        },
    )
    conn = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(
            fetchall=lambda: [{**vars(old), "source_id": 77}]
        )
    )
    observations = preserve_verified_eps(
        conn,
        dict(company_id=1, fiscal_year=2026, fiscal_quarter=2, filing_key=1),
        [current],
        {},
    )
    assert {o.earnings_scope for o in observations} == {"total", "continuing"}


@pytest.mark.parametrize("source_kind,expected", [("json", False), ("kap_html", True)])
def test_conflict_quarantine_preserves_history_and_manual_sources(
    database, source_kind, expected
):
    from services.earnings_per_share import quarantine_conflicting_eps_source

    conn, company, run = database
    filing = conn.execute(
        """INSERT INTO bronze.statement_filings
        (company_id,fiscal_year,fiscal_quarter,statement_type,source_filename,source_path,source_sha256,run_id)
        VALUES (%s,2024,1,'income_statement','test.xls','test.zip/test.xls','test',%s) RETURNING filing_key""",
        (company, run),
    ).fetchone()["filing_key"]
    obs = normalize_observation(
        period_start=date(2024, 1, 1),
        period_end=date(2024, 3, 31),
        period_kind="ytd",
        reported_eps=1,
    )
    store_eps_source(
        conn,
        company_id=company,
        year=2024,
        quarter=1,
        source_path="test",
        source_hash="test",
        source_kind=source_kind,
        payload={},
        observations=[obs],
        run_id=run,
        filing_key=filing,
    )
    row = dict(
        company_id=company, fiscal_year=2024, fiscal_quarter=1, filing_key=filing
    )
    assert (
        quarantine_conflicting_eps_source(conn, row, run, ValueError("Unknown units"))
        is False
    )
    assert (
        quarantine_conflicting_eps_source(
            conn, row, run, ValueError("EPS sources disagree")
        )
        is expected
    )
    assert conn.execute("SELECT COUNT(*) AS n FROM ops.active_eps_sources").fetchone()[
        "n"
    ] == (0 if expected else 1)
    assert (
        conn.execute("SELECT COUNT(*) AS n FROM bronze.eps_sources").fetchone()["n"]
        == 1
    )
    assert (
        conn.execute("SELECT COUNT(*) AS n FROM silver.eps_observations").fetchone()[
            "n"
        ]
        == 1
    )
