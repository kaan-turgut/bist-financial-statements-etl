from datetime import date, timedelta
from decimal import Decimal

from models.earnings_per_share import EpsObservation, calculate_history
from models.eps_capital import CapitalHistory, verified_share_basis
from models.financial_values import CpiSeries
from read_statements.eps import _nominal_values
from read_statements.eps_recovery import extract_recoverable_eps, numeric_cells
from services.eps_coverage import calculate_eps_coverage
from models.earnings_per_share import normalize_observation
from services.earnings_per_share import refresh_eps_postgres, store_eps_source
from services import postgres_financial_pipeline as financials
from psycopg.types.json import Jsonb
from types import SimpleNamespace
import pytest


def table_line(label, values):
    return label.ljust(76) + "".join(str(v).rjust(25) for v in values)


def test_mixed_currency_six_columns_preserve_empty_eps_cells():
    page = "\n".join(
        [
            "Tutarlar bin Türk Lirası olarak ifade edilmiştir.",
            table_line(
                "",
                [
                    "1 Ocak -",
                    "1 Ocak -",
                    "1 Temmuz -",
                    "1 Ocak -",
                    "1 Ocak -",
                    "1 Temmuz -",
                ],
            ),
            table_line("", ["30 Eylül 2026"] * 3 + ["30 Eylül 2025"] * 3),
            table_line(
                "",
                [
                    "Bin ABD Doları",
                    "Bin TL",
                    "Bin TL",
                    "Bin ABD Doları",
                    "Bin TL",
                    "Bin TL",
                ],
            ),
            table_line(
                "Ana Ortaklık Payları",
                [
                    "244.565",
                    "11.143.436",
                    "2.210.474",
                    "61.903",
                    "2.384.101",
                    "651.157",
                ],
            ),
            table_line(
                "PAY BAŞINA KAZANÇ (KAYIP)",
                ["", "1,6578", "0,3288", "", "0,3547", "0,0969"],
            ),
            "(1 TL nominal bedelli)",
        ]
    )
    observations, _ = extract_recoverable_eps([page], 3)
    assert [(o.period_kind, o.value) for o in observations] == [
        ("ytd", Decimal("1.6578")),
        ("quarter", Decimal(".3288")),
    ]


def test_calculate_basic_without_basic_eps_row_uses_parent_profit():
    page = "\n".join(
        [
            "Tutarlar Türk Lirası olarak ifade edilmiştir.",
            "23. PAY BAŞINA KAZANÇ",
            table_line("", ["1 Ocak -", "1 Ocak -", "1 Temmuz -", "1 Temmuz -"]),
            table_line(
                "", ["30 Eylül 2026", "30 Eylül 2025", "30 Eylül 2026", "30 Eylül 2025"]
            ),
            table_line("Ağırlıklı ortalama sayısı (tam değeri)", ["829.650.000"] * 4),
            table_line(
                "Net dönem zararı",
                ["(940.303.062)", "(94.477.340)", "(609.635.088)", "2.460.799"],
            ),
            table_line(
                "Kontrol gücü olmayan paylar",
                ["29.504.833", "3.325.571", "(5.662.876)", "(1.230.485)"],
            ),
            table_line(
                "Ana ortaklığa ait net dönem zararı",
                ["(969.807.895)", "(97.802.911)", "(603.972.212)", "3.691.285"],
            ),
            table_line(
                "Sulandırılmış pay başına kazanç (TL)",
                ["(1,169)", "(0,118)", "(0,728)", "0,004"],
            ),
            "Artırılan 34.150.000 TL nominal değerli paylar; birim nominal pay değeri 1 TL.",
        ]
    )
    obs, _ = extract_recoverable_eps([page], 3)
    basic = [o for o in obs if o.eps_kind == "basic"]
    assert len(basic) == 2
    assert basic[0].attributable_profit == Decimal("-969807895")
    assert basic[1].value == Decimal("-603972212") / Decimal("829650000")


def test_nominal_value_does_not_match_suffix_of_total_capital():
    assert _nominal_values(
        "34.150.000 TL nominal değerli paylar; birim nominal pay değeri 1 TL"
    ) == {Decimal(1)}
    assert numeric_cells("Malformed note".ljust(80) + "8,9,10") == []


def capital(rows):
    return CapitalHistory(
        [
            {"price_date": date.fromisoformat(day), "capital": Decimal(value)}
            for day, value in rows
        ]
    )


def test_calendar_weighting_includes_weekends_and_cash_change_dates():
    history = capital([("2026-01-02", 100), ("2026-01-05", 200)])
    value = history.period(date(2026, 1, 2), date(2026, 1, 6))
    assert value["weighted_capital"] == 140
    assert value["stable_basis"] is None


def test_no_future_capital_or_stale_carry():
    history = capital([("2026-01-05", 100)])
    assert history.period(date(2026, 1, 1), date(2026, 1, 10)) is None
    assert history.period(date(2026, 1, 5), date(2026, 1, 16)) is None
    estimate = history.period(
        date(2026, 1, 1), date(2026, 1, 10), allow_opening_estimate=True
    )
    assert estimate["weighted_capital"] == 100
    assert estimate["opening_assumption"]["observed_date"] == "2026-01-05"


def test_verified_basis_requires_disclosed_count_and_no_capital_change():
    start, end = date(2026, 1, 1), date(2026, 3, 31)
    history = capital([(str(start + timedelta(days=i)), 100) for i in range(90)])
    obs = EpsObservation(
        start, end, "quarter", attributable_profit=10, weighted_average_shares=95
    )
    assert verified_share_basis(obs, history).share_basis_id
    assert (
        verified_share_basis(
            EpsObservation(start, end, "quarter", reported_eps=1), history
        ).share_basis_id
        is None
    )
    too_many = EpsObservation(
        start, end, "quarter", attributable_profit=10, weighted_average_shares=200
    )
    assert verified_share_basis(too_many, history).share_basis_id is None


def test_cpi_reconciled_ttm_and_missing_month_rejection():
    quarters = [
        (date(2025, 4, 1), date(2025, 6, 30)),
        (date(2025, 7, 1), date(2025, 9, 30)),
        (date(2025, 10, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 3, 31)),
    ]
    history = {
        (2025 + (i == 3), (i + 1) % 4 + 1): [
            EpsObservation(
                start,
                end,
                "quarter",
                attributable_profit=100,
                weighted_average_shares=100,
                share_basis_id="verified",
                purchasing_power_date=end,
            )
        ]
        for i, (start, end) in enumerate(quarters)
    }
    rows = [
        {
            "observation_date": date(2025 + (m > 12), (m - 1) % 12 + 1, 1),
            "value": Decimal(1),
        }
        for m in range(6, 16)
    ]
    result = calculate_history(history, CpiSeries(rows))[
        (2026, 1, "basic", "ordinary", "total")
    ]
    assert result["value_ttm"] > 4
    assert result["details"]["ttm_method"] == "verified_four_quarters_cpi"
    assert (
        calculate_history(history, CpiSeries(rows[1:]))[
            (2026, 1, "basic", "ordinary", "total")
        ]["value_ttm"]
        is None
    )


def test_capital_estimates_are_separate_and_every_period_has_reason(database):
    conn, company, run = database
    conn.execute(
        """INSERT INTO gold.eps_values(calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,earnings_scope,calculation_details)
        VALUES (%s,%s,2026,1,'basic','ordinary','total','{}'),(%s,%s,2026,2,'basic','ordinary','total','{}')""",
        (run, company, run, company),
    )
    conn.execute(
        """INSERT INTO gold.financial_values(calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,value_quarter,value_ytd,period_start,period_end,measurement_basis,calculation_details)
        VALUES (%s,%s,'income_statement',2026,1,'distribution of profit (loss) for the period > ana ortaklık payları','ana ortaklık payları',10,10,'2026-01-01','2026-03-31','nominal','{"ytd_days":90}')""",
        (run, company),
    )
    h = capital([(str(date(2026, 1, 1) + timedelta(days=i)), 100) for i in range(90)])
    calculate_eps_coverage(conn, run, {company: h}, CpiSeries([]))
    result = conn.execute(
        "SELECT * FROM gold.eps_coverage ORDER BY fiscal_quarter"
    ).fetchall()
    assert len(result) == 2
    assert result[0]["estimated_value_quarter"] == Decimal(".1")
    assert result[0]["assessment"]["quality"]["quarter"]["status"] == "estimated"
    assert result[1]["assessment"]["quality"]["quarter"]["status"] == "review_required"
    assert (
        conn.execute("SELECT count(value_quarter) n FROM gold.eps_values").fetchone()[
            "n"
        ]
        == 0
    )


def published_eps_refresh_example(database):
    conn, company, run = database
    conn.execute(
        """INSERT INTO gold.financial_values(calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,period_start,period_end)
        VALUES (%s,%s,'income_statement',2023,4,'profit or loss > test','test','2024-01-01','2024-03-31')""",
        (run, company),
    )
    conn.execute(
        """INSERT INTO gold.eps_values(calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,earnings_scope,value_ttm,calculation_details)
        VALUES (%s,%s,2023,4,'basic','ordinary','total',1,'{}')""",
        (run, company),
    )
    conn.execute(
        "INSERT INTO ops.active_company_versions(dataset_name,company_id,run_id) VALUES('financials',%s,%s)",
        (company, run),
    )
    conn.execute(
        """INSERT INTO gold.financial_ratios(calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,value_ttm)
        VALUES (%s,%s,2023,4,'eps',1),(%s,%s,2023,4,'gross_margin',.25)""",
        (run, company, run, company),
    )
    source_run = financials._run_start(conn, "test_new_eps_source", {})
    obs = normalize_observation(
        period_start=date(2023, 4, 1),
        period_end=date(2024, 3, 31),
        period_kind="ytd",
        reported_eps=2,
    )
    store_eps_source(
        conn,
        company_id=company,
        year=2023,
        quarter=4,
        source_path="verified.json",
        source_hash="new-source",
        source_kind="json",
        payload={},
        observations=[obs],
        run_id=source_run,
    )
    instrument = conn.execute(
        "INSERT INTO metadata.instruments(company_id,symbol,is_primary) VALUES(%s,'TEST',true) RETURNING instrument_id",
        (company,),
    ).fetchone()["instrument_id"]
    conn.execute(
        "INSERT INTO silver.daily_prices(instrument_id,price_date,capital,source) VALUES(%s,'2024-03-29',100,'IsYatirim')",
        (instrument,),
    )
    conn.execute(
        """INSERT INTO bronze.price_observations(instrument_id,price_date,source,source_hash,run_id,source_payload)
        VALUES (%s,'2024-03-29','IsYatirim','price',%s,%s)""",
        (instrument, run, Jsonb({"at_the_date_close_price": 20})),
    )
    conn.commit()
    return conn, company, run


def test_eps_only_refresh_keeps_financial_rows_and_snapshots_previous_values(database):
    conn, company, run = published_eps_refresh_example(database)
    conn.execute(
        """INSERT INTO gold.eps_values(calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,earnings_scope,value_ttm,calculation_details)
        VALUES(%s,%s,2023,4,'basic','obsolete_class','total',999,'{}')""",
        (run, company),
    )
    conn.commit()
    before = conn.execute("SELECT * FROM gold.financial_values").fetchall()
    refresh = refresh_eps_postgres([SimpleNamespace(symbol="TEST")])
    assert conn.execute("SELECT * FROM gold.financial_values").fetchall() == before
    assert (
        conn.execute(
            "SELECT value_ttm FROM gold.current_eps_values WHERE eps_kind='basic' AND earnings_scope='total'"
        ).fetchone()["value_ttm"]
        == 2
    )
    assert conn.execute(
        "SELECT value_ttm FROM gold.current_financial_ratios WHERE ratio_code='gross_margin'"
    ).fetchone()["value_ttm"] == Decimal(".25")
    assert (
        conn.execute(
            "SELECT value_ttm FROM gold.current_financial_ratios WHERE ratio_code='price_earnings'"
        ).fetchone()["value_ttm"]
        == 10
    )
    snapshot = conn.execute(
        "SELECT previous_row FROM ops.eps_refresh_snapshots WHERE refresh_run_id=%s AND dataset_name='eps_values' AND previous_row->>'share_class'='ordinary'",
        (refresh,),
    ).fetchone()["previous_row"]
    assert snapshot["value_ttm"] == 1
    assert (
        conn.execute("SELECT run_id FROM ops.active_company_versions").fetchone()[
            "run_id"
        ]
        == run
    )
    assert (
        conn.execute(
            "SELECT count(*) n FROM gold.current_eps_values WHERE share_class='obsolete_class'"
        ).fetchone()["n"]
        == 0
    )


def test_failed_eps_only_refresh_rolls_back_derived_values_and_snapshots(
    database, monkeypatch
):
    conn, company, run = published_eps_refresh_example(database)
    import services.price_earnings as pe_service

    def fail(*args, **kwargs):
        raise RuntimeError("simulated publication failure")

    monkeypatch.setattr(pe_service, "calculate_price_earnings", fail)
    with pytest.raises(RuntimeError, match="publication failure"):
        refresh_eps_postgres([SimpleNamespace(symbol="TEST")])
    assert (
        conn.execute(
            "SELECT value_ttm FROM gold.current_eps_values WHERE eps_kind='basic' AND earnings_scope='total'"
        ).fetchone()["value_ttm"]
        == 1
    )
    assert (
        conn.execute("SELECT count(*) n FROM ops.eps_refresh_snapshots").fetchone()["n"]
        == 0
    )


def test_bank_parent_profit_requires_reconciliation_to_net_and_minority(database):
    conn, company, run = database
    conn.execute(
        """INSERT INTO gold.eps_values(calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,earnings_scope,calculation_details)
        VALUES(%s,%s,2026,1,'basic','ordinary','total','{}')""",
        (run, company),
    )
    for label, value in [
        ("net dönem karı (zararı)", 12),
        ("grubun karı (zararı)", 10),
        ("azınlık payları karı (zararı)", 2),
    ]:
        conn.execute(
            """INSERT INTO gold.financial_values(calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,value_quarter,value_ytd,period_start,period_end,measurement_basis,calculation_details)
            VALUES(%s,%s,'income_statement',2026,1,%s,%s,%s,%s,'2026-01-01','2026-03-31','nominal','{"ytd_days":90}')""",
            (run, company, "gelir ve gider kalemleri > " + label, label, value, value),
        )
    h = capital([(str(date(2026, 1, 1) + timedelta(days=i)), 100) for i in range(90)])
    calculate_eps_coverage(conn, run, {company: h}, CpiSeries([]))
    assert conn.execute("SELECT estimated_value_ytd FROM gold.eps_coverage").fetchone()[
        "estimated_value_ytd"
    ] == Decimal(".1")
    conn.execute(
        "UPDATE gold.financial_values SET value_ytd=3 WHERE item='azınlık payları karı (zararı)'"
    )
    calculate_eps_coverage(conn, run, {company: h}, CpiSeries([]))
    assert (
        conn.execute("SELECT estimated_value_ytd FROM gold.eps_coverage").fetchone()[
            "estimated_value_ytd"
        ]
        is None
    )
    conn.execute(
        "UPDATE gold.financial_values SET value_ytd=0,value_quarter=0 WHERE item='azınlık payları karı (zararı)'"
    )
    calculate_eps_coverage(conn, run, {company: h}, CpiSeries([]))
    assert (
        conn.execute("SELECT estimated_value_ytd FROM gold.eps_coverage").fetchone()[
            "estimated_value_ytd"
        ]
        is None
    )
    conn.execute(
        "DELETE FROM gold.financial_values WHERE item='net dönem karı (zararı)'"
    )
    calculate_eps_coverage(conn, run, {company: h}, CpiSeries([]))
    assert conn.execute("SELECT estimated_value_ytd FROM gold.eps_coverage").fetchone()[
        "estimated_value_ytd"
    ] == Decimal(".1")


def test_ocr_missing_system_tools_has_actionable_per_report_error(monkeypatch):
    import read_statements.eps as pdf

    monkeypatch.setattr(pdf.shutil, "which", lambda tool: None)
    with pytest.raises(ValueError, match="tesseract"):
        pdf.ocr_empty_pages(b"%PDF-", [""])


def test_nonfinite_capital_does_not_crash_or_produce_estimates():
    assert (
        capital([("2026-01-01", "NaN")]).period(date(2026, 1, 1), date(2026, 1, 5))
        is None
    )


def test_adjacent_eps_rows_do_not_share_currency_labels():
    page = "\n".join(
        [
            "Tutarlar Türk Lirası olarak ifade edilmiştir.",
            "23. Pay Başına Kazanç",
            table_line("", ["1 Ocak -", "1 Ocak -"]),
            table_line("", ["30 Haziran 2026", "30 Haziran 2025"]),
            table_line("Ana Ortaklık Payları", ["100", "200"]),
            table_line("Pay başına kazanç (TL)", ["1,00", "2,00"]),
            table_line("Sulandırılmış pay başına kazanç (KR)", ["100,00", "200,00"]),
            "(1 TL nominal bedelli)",
            "NOT 1. SERMAYE",
            "Paylar 1 TL nominal değerli paylardan oluşur.",
        ]
    )
    obs, _ = extract_recoverable_eps([page], 2)
    assert {(o.eps_kind, o.value) for o in obs} == {
        ("basic", Decimal(1)),
        ("diluted", Decimal(1)),
    }


def test_legacy_neighbor_currency_is_corrected_only_with_same_cell_and_explicit_unit():
    from services.earnings_per_share import preserve_verified_eps

    old = normalize_observation(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 6, 30),
        period_kind="ytd",
        reported_eps=1,
        eps_unit="KURUS",
        normalization={
            "validation": "explicit_nominal_pdf_layout",
            "source_page": 3,
            "source_column_index": 1,
            "source_eps_label": "Pay başına kazanç (TL)",
        },
    )
    current = normalize_observation(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 6, 30),
        period_kind="ytd",
        reported_eps=1,
        eps_unit="TRY",
        normalization={
            "validation": "explicit_nominal_pdf_layout",
            "source_page": 3,
            "source_column_index": 1,
            "source_eps_label": "Pay başına kazanç (TL)",
            "source_unit_evidence": "Pay başına kazanç (TL) (1 TL nominal bedelli)",
        },
    )
    conn = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(
            fetchall=lambda: [{**vars(old), "source_id": 77}]
        )
    )
    payload = {}
    result = preserve_verified_eps(
        conn,
        dict(company_id=1, fiscal_year=2026, fiscal_quarter=2, filing_key=1),
        [current],
        payload,
    )
    assert result == [current]
    assert payload["corrected_legacy_units"] == [
        {"source_id": 77, "from": "KURUS", "to": "TRY"}
    ]


def test_eps_note_reporting_nominal_overrides_legal_share_nominal():
    capital_page = "NOT 1. SERMAYE\nHer biri 0,01 TL nominal değerli hisseler."
    note = "\n".join(
        [
            "Tutarlar Türk Lirası olarak ifade edilmiştir.",
            "20. Pay Başına Kazanç",
            table_line("", ["1 Ocak -", "1 Ocak -"]),
            table_line("", ["31 Mart 2017", "31 Mart 2016"]),
            table_line("Dönem karı (TL)", ["11.022.872", "6.928.553"]),
            table_line(
                "Adi hisse senetlerinin ağırlıklı ortalama adedi",
                ["10.800.000", "10.800.000"],
            ),
            table_line("Pay başına kazanç (TL)", ["1,021", "0,642"]),
            table_line("Hissedarlara dağıtılan temettü", ["32.464.800", "44.496.000"]),
            table_line("Pay başına dağıtılan brüt temettü", ["3,006", "4,120"]),
            "(*) 1 TL nominal değerdeki hisseye isabet eden",
        ]
    )
    obs, _ = extract_recoverable_eps([capital_page, note], 1)
    assert len(obs) == 1
    assert obs[0].value == Decimal("1.021")
    assert obs[0].normalization["share_nominal_value"] == "1"


def test_generic_try_eps_without_local_nominal_basis_does_not_use_capital_note():
    capital_page = "NOT 1. SERMAYE\nHer biri 0,01 TL nominal değerli hisseler."
    face = "\n".join(
        [
            table_line("", ["1 Ocak -", "1 Ocak -"]),
            table_line("", ["31 Mart 2026", "31 Mart 2025"]),
            table_line("Dönem karı (TL)", ["100", "100"]),
            table_line("Pay başına kazanç (TL)", ["1,00", "1,00"]),
        ]
    )
    obs, _ = extract_recoverable_eps([capital_page, face], 1)
    assert obs == []


def test_diluted_weighted_share_count_does_not_create_basic_eps():
    page = "\n".join(
        [
            "Tutarlar Türk Lirası olarak ifade edilmiştir.",
            "23. Pay Başına Kazanç",
            table_line("", ["1 Ocak -", "1 Ocak -"]),
            table_line("", ["30 Haziran 2026", "30 Haziran 2025"]),
            table_line(
                "Sulandırılmış ağırlıklı ortalama hisse sayısı (tam değeri)",
                ["1.000", "1.000"],
            ),
            table_line("Ana ortaklığa ait net dönem karı", ["900", "900"]),
            table_line("Sulandırılmış pay başına kazanç (TL)", ["0,900", "0,900"]),
            "Birim nominal pay değeri 1 TL.",
        ]
    )
    obs, _ = extract_recoverable_eps([page], 2)
    assert not any(o.eps_kind == "basic" for o in obs)


@pytest.mark.parametrize("instrument_count,expected", [(1, Decimal(".1")), (2, None)])
def test_reported_paid_capital_is_an_explicit_proxy_and_rejects_multiple_classes(
    database, instrument_count, expected
):
    conn, company, run = database
    for i in range(instrument_count):
        conn.execute(
            "INSERT INTO metadata.instruments(company_id,symbol) VALUES(%s,%s)",
            (company, "CLASS" + str(i)),
        )
    conn.execute(
        """INSERT INTO gold.eps_values(calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,earnings_scope,calculation_details)
        VALUES(%s,%s,2026,1,'basic','ordinary','total','{}')""",
        (run, company),
    )
    for statement, path, item, value in [
        (
            "income_statement",
            "distribution of profit (loss) for the period > ana ortaklık payları",
            "ana ortaklık payları",
            10,
        ),
        ("balance_sheet", "equity > ödenmiş sermaye", "ödenmiş sermaye", 100),
    ]:
        conn.execute(
            """INSERT INTO gold.financial_values(calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,value_quarter,value_ytd,period_start,period_end,measurement_basis,calculation_details)
            VALUES(%s,%s,%s,2026,1,%s,%s,%s,%s,'2026-01-01','2026-03-31','nominal','{"ytd_days":90}')""",
            (run, company, statement, path, item, value, value),
        )
    calculate_eps_coverage(conn, run, {}, CpiSeries([]))
    row = conn.execute("SELECT * FROM gold.eps_coverage").fetchone()
    assert row["estimated_value_ytd"] == expected
    if expected is not None:
        assert (
            row["assessment"]["evidence"]["ytd"]["denominator_method"]
            == "period_end_reported_paid_in_capital_proxy"
        )
        assert row["assessment"]["quality"]["ytd"]["status"] == "estimated"
    assert (
        conn.execute("SELECT value_ytd FROM gold.eps_values").fetchone()["value_ytd"]
        is None
    )


@pytest.mark.parametrize(
    "kind,expected", [("Konsolide Olmayan", Decimal(".1")), ("Konsolide", None)]
)
def test_standalone_legacy_profit_requires_linked_nonconsolidated_filing(
    database, kind, expected
):
    conn, company, run = database
    filing = conn.execute(
        """INSERT INTO bronze.statement_filings(company_id,fiscal_year,fiscal_quarter,statement_type,source_filename,source_path,source_sha256,run_id)
        VALUES(%s,2026,1,'income_statement','test.xls','test.zip/test.xls','solo',%s) RETURNING filing_key""",
        (company, run),
    ).fetchone()["filing_key"]
    conn.execute(
        """INSERT INTO ops.active_filing_versions(company_id,statement_type,fiscal_year,fiscal_quarter,filing_key,processing_run_id)
        VALUES(%s,'income_statement',2026,1,%s,%s)""",
        (company, filing, run),
    )
    conn.execute(
        """INSERT INTO silver.financial_observations(company_id,statement_type,fiscal_year,fiscal_quarter,processing_run_id,filing_key,period_start,period_end,line_item_path,item,amount,report_kind)
        VALUES(%s,'income_statement',2026,1,%s,%s,'2026-01-01','2026-03-31','net dönem karı (zararı)','net dönem karı (zararı)',10,%s)""",
        (company, run, filing, kind),
    )
    conn.execute(
        """INSERT INTO gold.eps_values(calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,earnings_scope,calculation_details)
        VALUES(%s,%s,2026,1,'basic','ordinary','total','{}')""",
        (run, company),
    )
    for statement, item, amount in [
        ("income_statement", "net dönem karı (zararı)", 10),
        ("balance_sheet", "ödenmiş sermaye", 100),
    ]:
        conn.execute(
            """INSERT INTO gold.financial_values(calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,value_quarter,value_ytd,period_start,period_end,measurement_basis,calculation_details)
            VALUES(%s,%s,%s,2026,1,%s,%s,%s,%s,'2026-01-01','2026-03-31','nominal',%s)""",
            (
                run,
                company,
                statement,
                item,
                item,
                amount,
                amount,
                Jsonb({"ytd_days": 90, "source_filing_keys": [filing]}),
            ),
        )
    calculate_eps_coverage(conn, run, {}, CpiSeries([]))
    assert (
        conn.execute("SELECT estimated_value_ytd FROM gold.eps_coverage").fetchone()[
            "estimated_value_ytd"
        ]
        == expected
    )
    if kind == "Konsolide":
        conn.execute(
            """INSERT INTO gold.financial_values(calculation_run_id,company_id,statement_type,fiscal_year,fiscal_quarter,line_item_path,item,value_quarter,value_ytd,period_start,period_end,measurement_basis,calculation_details)
            VALUES(%s,%s,'income_statement',2026,1,'kontrol gücü olmayan paylar','kontrol gücü olmayan paylar',2,2,'2026-01-01','2026-03-31','nominal',%s)""",
            (run, company, Jsonb({"ytd_days": 90, "source_filing_keys": [filing]})),
        )
        calculate_eps_coverage(conn, run, {}, CpiSeries([]))
        result = conn.execute("SELECT * FROM gold.eps_coverage").fetchone()
        assert result["estimated_value_ytd"] == Decimal(".08")
        assert (
            result["assessment"]["evidence"]["ytd"]["parent_profit_reconstruction"][
                "method"
            ]
            == "net_profit_less_disclosed_minority_profit"
        )
