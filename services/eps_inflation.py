"""Express the best-available EPS in the financial dataset's CPI target month."""

from collections import defaultdict
from datetime import date

from psycopg.types.json import Jsonb

from models.financial_values import CpiSeries


def _date(value):
    if value in (None, "None"):
        return None
    return date.fromisoformat(value) if isinstance(value, str) else value


def adjust_eps(value, source_date, target_date, cpi, quality):
    """Rebase a monetary per-share value, keeping missing inputs and provenance."""
    evidence = {
        "input_value": str(value) if value is not None else None,
        "quality": quality,
        "source_date": str(source_date) if source_date else None,
        "target_date": str(target_date) if target_date else None,
        "cpi_factor": None,
        "unavailable_reason": None,
    }
    if value is None:
        evidence["unavailable_reason"] = "missing_eps"
        return None, evidence
    if source_date is None or target_date is None:
        evidence["unavailable_reason"] = "missing_or_ambiguous_inflation_basis"
        return None, evidence
    factor = cpi.factor(source_date, target_date)
    if factor is None:
        evidence["unavailable_reason"] = "incomplete_cpi"
        return None, evidence
    evidence["cpi_factor"] = str(factor)
    return value * factor, evidence


def _source_date(row, kind, observations):
    """Use the selected field's own basis, never an unrelated estimate's basis."""
    if row["verified_" + kind] is None:
        evidence = row["assessment"].get("evidence", {}).get(kind, {})
        basis = evidence.get("measurement_basis")
        value = (
            evidence.get("purchasing_power_date")
            if basis == "inflation_adjusted"
            else (evidence.get("period_end") if basis == "nominal" else None)
        )
        return _date(value), "capital_estimate_evidence"

    details = row["calculation_details"]
    # Derived TTM already expresses all component profits in this purchasing power.
    if kind == "ttm" and details.get("purchasing_power_date_ttm"):
        return _date(details["purchasing_power_date_ttm"]), "verified_ttm_calculation"
    source_ids = set(details.get("source_ids", []))
    selected = [o for o in observations if o["source_id"] in source_ids]
    preferred_kind = "quarter" if kind == "quarter" else "ytd"
    preferred = [o for o in selected if o["period_kind"] == preferred_kind]
    # Q1 quarter and YTD are equivalent; derived quarters use the current YTD basis.
    if not preferred and (kind in ("quarter", "ttm") or row["fiscal_quarter"] == 1):
        preferred = selected
    bases = {
        o["purchasing_power_date"]
        if o["purchasing_power_date"] is not None
        else (o["period_end"] if o["measurement_basis"] == "nominal" else None)
        for o in preferred
    }
    return (next(iter(bases)) if len(bases) == 1 else None), "matching_eps_disclosure"


def publish_eps_inflation_ratio(conn, run_id, company_ids=None, cpi=None):
    """Add eps_inflation_adj without changing EPS, P/E, or source observations.

    For nominal reports, period-end CPI rebasing is an analytical convention,
    matching financial_values.value_real; it does not recreate IAS 29 accounts.
    """
    if cpi is None:
        cpi = CpiSeries(
            conn.execute(
                "SELECT observation_date,value FROM silver.monthly_macro_data WHERE series_code='CPI_MONTHLY_RATE'"
            ).fetchall()
        )
    condition = " AND r.company_id=ANY(%s)" if company_ids is not None else ""
    rows = conn.execute(
        """SELECT r.company_id,r.fiscal_year,r.fiscal_quarter,
          r.value_quarter,r.value_ytd,r.value_ttm,
          e.value_quarter verified_quarter,e.value_ytd verified_ytd,e.value_ttm verified_ttm,
          e.calculation_details,c.assessment,
          f.target_year,f.target_month,f.target_count
        FROM gold.financial_ratios r
        JOIN gold.eps_values e ON e.calculation_run_id=r.calculation_run_id AND e.company_id=r.company_id
          AND e.fiscal_year=r.fiscal_year AND e.fiscal_quarter=r.fiscal_quarter
          AND e.eps_kind='basic' AND e.share_class='ordinary' AND e.earnings_scope='total'
        JOIN gold.eps_coverage c ON c.calculation_run_id=r.calculation_run_id AND c.company_id=r.company_id
          AND c.fiscal_year=r.fiscal_year AND c.fiscal_quarter=r.fiscal_quarter
        LEFT JOIN (
          SELECT company_id,fiscal_year,fiscal_quarter,min(inflation_base_year) target_year,
            min(inflation_base_month) target_month,
            count(DISTINCT (inflation_base_year,inflation_base_month)) target_count
          FROM gold.financial_values WHERE calculation_run_id=%s
          GROUP BY company_id,fiscal_year,fiscal_quarter
        ) f ON f.company_id=r.company_id AND f.fiscal_year=r.fiscal_year AND f.fiscal_quarter=r.fiscal_quarter
        WHERE r.calculation_run_id=%s AND r.ratio_code='eps'"""
        + condition,
        (run_id, run_id, company_ids) if company_ids is not None else (run_id, run_id),
    ).fetchall()
    selected_ids = sorted({r["company_id"] for r in rows})
    sources = defaultdict(list)
    for observation in conn.execute(
        """SELECT s.company_id,s.fiscal_year,s.fiscal_quarter,s.source_id,
          s.period_kind,s.period_end,COALESCE(s.purchasing_power_date,
            CASE WHEN c.measurement_basis='inflation_adjusted' THEN c.purchasing_power_date END) purchasing_power_date,
          c.measurement_basis
        FROM silver.current_eps_observations s JOIN bronze.eps_sources b USING(source_id)
        LEFT JOIN ops.active_filing_versions a ON a.filing_key=b.filing_key AND a.statement_type='income_statement'
        LEFT JOIN silver.financial_report_context c ON c.filing_key=a.filing_key AND c.processing_run_id=a.processing_run_id
        WHERE s.company_id=ANY(%s) AND s.eps_kind='basic'
          AND s.share_class='ordinary' AND s.earnings_scope='total'""",
        (selected_ids,),
    ):
        sources[
            (
                observation["company_id"],
                observation["fiscal_year"],
                observation["fiscal_quarter"],
            )
        ].append(observation)
    for row in rows:
        key = (row["company_id"], row["fiscal_year"], row["fiscal_quarter"])
        target = (
            date(row["target_year"], row["target_month"], 1)
            if (row["target_count"] == 1 and row["target_year"] and row["target_month"])
            else None
        )
        metadata = {
            "method": "eps_times_target_cpi_over_source_cpi",
            "unit": "TRY_per_1_TRY_nominal",
            "inflation_base_year": target.year if target else None,
            "inflation_base_month": target.month if target else None,
            "nominal_report_convention": "period_end_cpi_rebasing_not_ias29_restatement",
        }
        values = []
        for kind in ("quarter", "ytd", "ttm"):
            source_date, basis_source = _source_date(row, kind, sources[key])
            quality = (
                "verified"
                if row["verified_" + kind] is not None
                else (
                    "estimated"
                    if row["value_" + kind] is not None
                    else "review_required"
                )
            )
            value, evidence = adjust_eps(
                row["value_" + kind], source_date, target, cpi, quality
            )
            evidence["basis_source"] = basis_source
            metadata[kind] = evidence
            values.append(value)
        conn.execute(
            """UPDATE gold.eps_coverage SET inflation_adjustment=%s
            WHERE calculation_run_id=%s AND company_id=%s AND fiscal_year=%s AND fiscal_quarter=%s""",
            (Jsonb(metadata), run_id, *key),
        )
        conn.execute(
            """INSERT INTO gold.financial_ratios
            (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,value_quarter,value_ytd,value_ttm)
            VALUES (%s,%s,%s,%s,'eps_inflation_adj',%s,%s,%s)
            ON CONFLICT (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code)
            DO UPDATE SET value_quarter=EXCLUDED.value_quarter,value_ytd=EXCLUDED.value_ytd,
              value_ttm=EXCLUDED.value_ttm,quarter_yoy_change=NULL,quarter_qoq_change=NULL,
              ytd_yoy_change=NULL,ttm_qoq_change=NULL,calculated_at=now()""",
            (run_id, *key, *values),
        )
    return len(rows)
