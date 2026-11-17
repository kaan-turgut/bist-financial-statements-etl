"""One dashboard EPS code, with per-field fallback and separate provenance."""

from decimal import Decimal

import pytest
from psycopg.errors import CheckViolation
from psycopg.types.json import Jsonb

from paths import PROJECT_ROOT
from services.earnings_per_share import (
    calculate_eps_changes,
    calculate_eps_ratios,
    publish_eps_ratio,
)


def seed_eps(conn, company, run):
    for kind, scope, q, ytd, ttm in (
        ('basic', 'total', 0, None, -2),
        ('diluted', 'total', 999, 999, 999),
        ('basic', 'continuing', 888, 888, 888),
    ):
        conn.execute(
            """INSERT INTO gold.eps_values
            (calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,
             share_class,earnings_scope,value_quarter,value_ytd,value_ttm,calculation_details)
            VALUES (%s,%s,2024,4,%s,'ordinary',%s,%s,%s,%s,'{}')""",
            (run, company, kind, scope, q, ytd, ttm),
        )
    conn.execute(
        """INSERT INTO gold.eps_coverage
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,
         estimated_value_quarter,estimated_value_ytd,estimated_value_ttm,assessment)
        VALUES (%s,%s,2024,4,10,20,30,%s)""",
        (run, company, Jsonb({'quality': {'ytd': {'status': 'estimated'}}})),
    )


def test_single_eps_prefers_verified_per_field_including_zero_and_losses(database):
    conn, company, run = database
    seed_eps(conn, company, run)
    before = conn.execute('SELECT * FROM gold.eps_values').fetchall()
    publish_eps_ratio(conn, run)
    calculate_eps_changes(conn, run)
    row = conn.execute("SELECT * FROM gold.financial_ratios WHERE ratio_code='eps'").fetchone()
    assert row['ratio_code'] == 'eps'
    assert (row['value_quarter'], row['value_ytd'], row['value_ttm']) == (0, 20, -2)
    assert conn.execute('SELECT * FROM gold.eps_values').fetchall() == before
    assert conn.execute('SELECT count(*) n FROM gold.financial_ratios').fetchone()['n'] == 1

    # Re-running uses fresh estimates and retires stale values/growth.
    conn.execute('UPDATE gold.eps_coverage SET estimated_value_ytd=NULL')
    conn.execute('UPDATE gold.financial_ratios SET ytd_yoy_change=123')
    publish_eps_ratio(conn, run)
    row = conn.execute("SELECT * FROM gold.financial_ratios WHERE ratio_code='eps'").fetchone()
    assert row['value_ytd'] is None
    assert row['ytd_yoy_change'] is None


def test_normal_gold_calculation_publishes_estimate_under_eps_only(database):
    conn, company, run = database
    conn.execute(
        """INSERT INTO gold.financial_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,statement_type,
         line_item_path,item,value_quarter,value_ytd,period_start,period_end,measurement_basis,calculation_details)
        VALUES (%s,%s,2024,1,'income_statement',
          'distribution of profit (loss) for the period > ana ortaklık payları','ana ortaklık payları',
          10,10,'2024-01-01','2024-03-31','nominal','{"ytd_days":91}')""", (run, company),
    )
    conn.execute(
        """INSERT INTO gold.financial_values
        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,statement_type,
         line_item_path,item,value_quarter,period_end)
        VALUES (%s,%s,2024,1,'balance_sheet','paid capital','ödenmiş sermaye',100,'2024-03-31')""",
        (run, company),
    )
    calculate_eps_ratios(conn, run)
    row = conn.execute("SELECT * FROM gold.financial_ratios WHERE ratio_code='eps'").fetchone()
    assert row['ratio_code'] == 'eps'
    assert row['value_quarter'] == row['value_ytd'] == Decimal('.1')
    assert conn.execute("SELECT value_ytd FROM gold.eps_values WHERE eps_kind='basic' AND earnings_scope='total'").fetchone()['value_ytd'] is None
    assert conn.execute("SELECT assessment->'quality'->'ytd'->>'status' quality FROM gold.eps_coverage").fetchone()['quality'] == 'estimated'
    # Gold's published view and canonical ratio agree, after table precision.
    conn.execute("INSERT INTO ops.active_company_versions(dataset_name,company_id,run_id) VALUES ('financials',%s,%s)", (company, run))
    assert conn.execute('SELECT best_available_ytd FROM gold.current_eps_coverage').fetchone()['best_available_ytd'] == row['value_ytd']


def test_single_eps_publication_respects_company_scope(database):
    conn, company, run = database
    seed_eps(conn, company, run)
    publish_eps_ratio(conn, run, [])
    assert conn.execute('SELECT count(*) n FROM gold.financial_ratios').fetchone()['n'] == 0
    publish_eps_ratio(conn, run, [company])
    assert conn.execute('SELECT count(*) n FROM gold.financial_ratios').fetchone()['n'] == 1


def test_migration_preserves_removed_types_and_rejects_future_extra_codes(database):
    conn, company, run = database
    seed_eps(conn, company, run)
    conn.execute('ALTER TABLE gold.financial_ratios DROP CONSTRAINT financial_ratios_single_eps_code')
    for code in ('eps', 'eps_diluted', 'eps_continuing', 'eps_continuing_diluted', 'gross_margin'):
        conn.execute(
            """INSERT INTO gold.financial_ratios
            (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,value_ytd)
            VALUES (%s,%s,2024,4,%s,7)""", (run, company, code),
        )
    conn.execute((PROJECT_ROOT / 'database/migrations/016_single_eps_ratio.sql').read_text())
    rows = conn.execute('SELECT ratio_code,value_ytd FROM gold.financial_ratios ORDER BY ratio_code').fetchall()
    assert rows == [{'ratio_code': 'eps', 'value_ytd': 20}, {'ratio_code': 'gross_margin', 'value_ytd': 7}]
    assert conn.execute("SELECT count(*) n FROM ops.eps_refresh_snapshots WHERE previous_row->>'ratio_code' LIKE 'eps%'").fetchone()['n'] == 4
    assert conn.execute('SELECT count(*) n FROM gold.eps_values').fetchone()['n'] == 3
    with pytest.raises(CheckViolation), conn.transaction():
        conn.execute(
            """INSERT INTO gold.financial_ratios
            (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code)
            VALUES (%s,%s,2024,4,'eps_diluted')""", (run, company),
        )
