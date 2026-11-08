"""Retain distinct related/unrelated borrowing leaves in new and legacy data."""

import zipfile
from decimal import Decimal
from types import SimpleNamespace

import pytest

from read_statements.borrowing_paths import borrowing_path_overrides
from read_statements.parse_statement import parse_statement
from services.financial_values import calculate_financial_values
from services import postgres_financial_pipeline as pipeline
from tests.test_financial_measurement import fact, values


@pytest.mark.parametrize("root,leaf", [
    ("Kısa Vadeli Borçlanmalar", "Diğer Kısa Vadeli Borçlanmalar"),
    ("Uzun Vadeli Borçlanmaların Kısa Vadeli Kısımları", "Diğer Uzun Vadeli Borçlanmaların Kısa Vadeli Kısımları"),
])
def test_new_parser_keeps_counterparty_parent_for_other_borrowings(tmp_path, root, leaf):
    labels = [(root, 100), (f"İlişkili Taraflardan {root}", 0), (leaf, 0),
              (f"İlişkili Olmayan Taraflardan {root}", 100), (leaf, 100)]
    html = "<div>Yıl:2024 Periyot:1</div><table class='financial-table'>"
    html += "<tr><td class='context-header'><div class='gwt-Label'>31.03.2024</div></td></tr>"
    html += "<tr><td><div class='gwt-Label'>Bilanço</div></td></tr>"
    for index, (label, amount) in enumerate(labels):
        html += f"""<tr class='general_role_210000-row-{index} data-input-row'>
          <td><div class='gwt-Label'>{label}</div></td>
          <td class='taxonomy-context-value'><div class='gwt-Label'>{amount}</div></td></tr>"""
    html += "</table>"
    archive = tmp_path / "TEST_2024.zip"
    member = "TEST_123_2024_1.xls"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(member, html)
    frame = parse_statement(f"{archive}/{member}", SimpleNamespace(symbol="TEST")).df
    leaves = frame[frame["item"] == leaf.lower()]
    assert len(leaves) == 2
    paths = leaves["line_item_path"].tolist()
    assert paths[0] != paths[1]
    assert " > related-party " in paths[0]
    assert " > unrelated-party " in paths[1]


def bronze_rows():
    root = "short-term liabilities > short-term borrowings"
    return [
        dict(filing_key=1, source_row_id="210000:130", item="ilişkili taraflardan kısa vadeli borçlanmalar",
             line_item_path=root + " > related-party short-term borrowings"),
        dict(filing_key=1, source_row_id="210000:134", item="diğer kısa vadeli borçlanmalar",
             line_item_path=root + " > other short-term borrowings"),
        dict(filing_key=1, source_row_id="210000:135", item="ilişkili olmayan taraflardan kısa vadeli borçlanmalar",
             line_item_path=root + " > unrelated-party short-term borrowings"),
        dict(filing_key=1, source_row_id="210000:139", item="diğer kısa vadeli borçlanmalar",
             line_item_path=root + " > other short-term borrowings"),
    ]


def test_legacy_paths_are_recovered_from_parents_even_when_amounts_are_equal():
    rows = bronze_rows()
    overrides = borrowing_path_overrides(rows)
    assert overrides[(1, "210000:134")] != overrides[(1, "210000:139")]
    observations = [fact(1, amount, cumulative=False, statement_type="balance_sheet",
                         line_item_path=overrides[(1, row["source_row_id"])],
                         item=row["item"], source_row_id=row["source_row_id"])
                    for row, amount in [(rows[1], 0), (rows[3], 0)]]
    assert len(values(observations)) == 2


def test_missing_parent_and_new_total_never_reuse_a_stale_branch():
    rows = bronze_rows()
    assert borrowing_path_overrides([rows[1]]) == {}
    reset = dict(rows[0], item="kısa vadeli borçlanmalar",
                 line_item_path="short-term liabilities > short-term borrowings")
    assert borrowing_path_overrides([rows[0], reset, rows[1]]) == {}
    other_filing = dict(rows[1], filing_key=2)
    assert borrowing_path_overrides([rows[0], other_filing]) == {}


def test_actual_conflicts_still_raise_instead_of_choosing_or_summing():
    with pytest.raises(ValueError, match="Conflicting observations"):
        values([fact(1, 100), fact(1, 200)])


def test_gold_restores_legacy_paths_without_changing_silver(database):
    conn, company_id, run = database
    filing = conn.execute("""INSERT INTO bronze.statement_filings
        (company_id,fiscal_year,fiscal_quarter,statement_type,source_filename,source_path,source_sha256,run_id)
        VALUES (%s,2024,1,'balance_sheet','test.xls','test.zip/test.xls','test',%s) RETURNING filing_key""",
        (company_id, run)).fetchone()["filing_key"]
    rows = bronze_rows()
    for index, row in enumerate(rows):
        conn.execute("""INSERT INTO bronze.statement_rows
            (filing_key,source_row_number,source_row_id,item,line_item_path,source_date,raw_value)
            VALUES (%s,%s,%s,%s,%s,'31.03.2024',0)""",
            (filing, index, row["source_row_id"], row["item"], row["line_item_path"]))
    for row, amount in [(rows[1], 0), (rows[3], 1500332)]:
        conn.execute("""INSERT INTO silver.financial_observations
            (company_id,filing_key,processing_run_id,statement_type,fiscal_year,fiscal_quarter,
             period_start,period_end,line_item_path,item,amount,source_row_id)
            VALUES (%s,%s,%s,'balance_sheet',2024,1,'2024-03-31','2024-03-31',%s,%s,%s,%s)""",
            (company_id, filing, run, row["line_item_path"], row["item"], amount, row["source_row_id"]))
    conn.execute("""INSERT INTO ops.active_filing_versions
        (company_id,statement_type,fiscal_year,fiscal_quarter,filing_key,processing_run_id)
        VALUES (%s,'balance_sheet',2024,1,%s,%s)""", (company_id, filing, run))
    calculate_financial_values(conn, run, 2024, 12, None, pipeline.CASH_BALANCE_ITEMS)
    gold = conn.execute("SELECT line_item_path,value_quarter,calculation_details FROM gold.financial_values ORDER BY value_quarter").fetchall()
    assert len(gold) == 2
    assert gold[0]["value_quarter"] == 0
    assert " > related-party " in gold[0]["line_item_path"]
    assert gold[1]["value_quarter"] == Decimal(1500332)
    assert " > unrelated-party " in gold[1]["line_item_path"]
    assert gold[1]["calculation_details"]["source_line_item_path"] == rows[3]["line_item_path"]
    assert conn.execute("SELECT COUNT(DISTINCT line_item_path) AS n FROM silver.financial_observations").fetchone()["n"] == 1
    assert conn.execute("SELECT affected_rows FROM ops.data_quality_results WHERE check_name='borrowing_counterparty_paths_restored'").fetchone()["affected_rows"] == 2
