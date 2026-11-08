"""Assess every published period and expose capital estimates separately."""

import hashlib
from collections import defaultdict
from datetime import timedelta

from psycopg.types.json import Jsonb

from models.eps_capital import CapitalHistory
from models.financial_values import CpiSeries

ZERO_MINORITY = """COALESCE((SELECT bool_and(COALESCE(minority.value_ytd=0
      AND (g.value_quarter IS NULL OR minority.value_quarter=0),false))
    FROM gold.financial_values minority
    WHERE minority.calculation_run_id=g.calculation_run_id AND minority.company_id=g.company_id
      AND minority.fiscal_year=g.fiscal_year AND minority.fiscal_quarter=g.fiscal_quarter
      AND minority.statement_type='income_statement' AND minority.period_end=g.period_end
      AND minority.line_item_path IN ('kontrol gücü olmayan paylar','azınlık payları karı (zararı)',
        'distribution of profit (loss) for the period > kontrol gücü olmayan paylar',
        'gelir ve gider kalemleri > azınlık payları karı (zararı)')
    ),false)"""


def load_capital_histories(conn, company_ids):
    instruments = conn.execute(
        """SELECT company_id,min(instrument_id) instrument_id
        FROM metadata.instruments WHERE company_id=ANY(%s)
        GROUP BY company_id HAVING count(*)=1 AND bool_and(currency='TRY')""",
        (company_ids,),
    ).fetchall()
    owners = {r["instrument_id"]: r["company_id"] for r in instruments}
    rows = defaultdict(list)
    for row in conn.execute(
        """SELECT instrument_id,price_date,capital FROM silver.daily_prices
        WHERE instrument_id=ANY(%s) AND source='IsYatirim' ORDER BY instrument_id,price_date""",
        (list(owners),),
    ):
        rows[owners[row["instrument_id"]]].append(row)
    return {company: CapitalHistory(values) for company, values in rows.items()}


def calculate_eps_coverage(conn, run_id, capital=None, cpi=None, company_ids=None):
    rows = conn.execute(
        """SELECT e.company_id,e.fiscal_year,e.fiscal_quarter,
        e.value_quarter,e.value_ytd,e.value_ttm
        FROM gold.eps_values e WHERE e.calculation_run_id=%s
        AND e.eps_kind='basic' AND e.earnings_scope='total' AND e.share_class='ordinary'
        """
        + (" AND e.company_id=ANY(%s)" if company_ids is not None else "")
        + " ORDER BY e.company_id,e.fiscal_year,e.fiscal_quarter",
        (run_id, company_ids) if company_ids is not None else (run_id,),
    ).fetchall()
    if capital is None:
        capital = load_capital_histories(conn, sorted({r["company_id"] for r in rows}))
    if cpi is None:
        cpi = CpiSeries(
            conn.execute(
                "SELECT observation_date,value FROM silver.monthly_macro_data WHERE series_code='CPI_MONTHLY_RATE'"
            ).fetchall()
        )
    candidates = defaultdict(list)
    for row in conn.execute(
        f"""SELECT company_id,fiscal_year,fiscal_quarter,value_quarter,value_ytd,
        period_start,period_end,measurement_basis,purchasing_power_date,calculation_details,financial_value_id
        FROM gold.financial_values g WHERE calculation_run_id=%s AND statement_type='income_statement'
        AND ((item='ana ortaklık payları' AND starts_with(line_item_path,'distribution of profit (loss) for the period >'))
        OR (line_item_path='gelir ve gider kalemleri > grubun karı (zararı)' AND EXISTS (
            SELECT 1 FROM gold.financial_values n JOIN gold.financial_values m
             ON m.calculation_run_id=n.calculation_run_id AND m.company_id=n.company_id
             AND m.fiscal_year=n.fiscal_year AND m.fiscal_quarter=n.fiscal_quarter
             AND m.line_item_path='gelir ve gider kalemleri > azınlık payları karı (zararı)'
            WHERE n.calculation_run_id=g.calculation_run_id AND n.company_id=g.company_id
             AND n.fiscal_year=g.fiscal_year AND n.fiscal_quarter=g.fiscal_quarter
             AND n.line_item_path='gelir ve gider kalemleri > net dönem karı (zararı)'
             AND n.period_end=g.period_end AND m.period_end=g.period_end
             AND n.value_ytd=g.value_ytd+m.value_ytd
             AND (g.value_quarter IS NULL OR n.value_quarter=g.value_quarter+m.value_quarter)
        )) OR (g.line_item_path IN ('gelir ve gider kalemleri > net dönem karı (zararı)','net dönem karı (zararı)')
            AND NOT EXISTS(SELECT 1 FROM gold.financial_values parent
              WHERE parent.calculation_run_id=g.calculation_run_id AND parent.company_id=g.company_id
                AND parent.fiscal_year=g.fiscal_year AND parent.fiscal_quarter=g.fiscal_quarter
                AND parent.statement_type='income_statement' AND parent.item='ana ortaklık payları'
                AND starts_with(parent.line_item_path,'distribution of profit (loss) for the period >'))
            AND NOT EXISTS(SELECT 1 FROM gold.financial_values bank_parent
              WHERE bank_parent.calculation_run_id=g.calculation_run_id AND bank_parent.company_id=g.company_id
                AND bank_parent.fiscal_year=g.fiscal_year AND bank_parent.fiscal_quarter=g.fiscal_quarter
                AND bank_parent.line_item_path='gelir ve gider kalemleri > grubun karı (zararı)')
            AND (EXISTS(SELECT 1 FROM silver.current_financial_observations s
              WHERE s.company_id=g.company_id AND s.fiscal_year=g.fiscal_year
              AND s.fiscal_quarter=g.fiscal_quarter AND s.statement_type='income_statement'
              AND s.report_kind='Konsolide Olmayan' AND s.item=g.item
              AND s.filing_key IN (SELECT jsonb_array_elements_text(g.calculation_details->'source_filing_keys')::bigint))
              OR {ZERO_MINORITY}))
          OR (g.line_item_path='gelir ve gider kalemleri > grubun karı (zararı)'
            AND {ZERO_MINORITY} AND NOT EXISTS(SELECT 1 FROM gold.financial_values net
              WHERE net.calculation_run_id=g.calculation_run_id AND net.company_id=g.company_id
                AND net.fiscal_year=g.fiscal_year AND net.fiscal_quarter=g.fiscal_quarter
                AND net.line_item_path IN ('gelir ve gider kalemleri > net dönem karı (zararı)','net dönem karı (zararı)')
                AND net.value_ytd IS NOT NULL)))""",
        (run_id,),
    ):
        candidates[
            (row["company_id"], row["fiscal_year"], row["fiscal_quarter"])
        ].append(row)
    # Equal financial identities can have multiple auxiliary source columns;
    # differing amounts/dates are not resolved by selecting an arbitrary row.
    profits = {}
    for key, values in candidates.items():
        signatures = {
            (
                r["value_quarter"],
                r["value_ytd"],
                r["period_start"],
                r["period_end"],
                r["measurement_basis"],
                r["purchasing_power_date"],
            )
            for r in values
        }
        if len(signatures) == 1:
            profits[key] = values[0]
    # Some old consolidated templates omit the parent-profit line entirely.
    # Reconstruct it only from explicit, unambiguous net and minority amounts
    # in the same filing and measurement basis; missing minority is not zero.
    missing_companies = sorted(
        {
            r["company_id"]
            for r in rows
            if (r["company_id"], r["fiscal_year"], r["fiscal_quarter"]) not in profits
        }
    )
    components = defaultdict(lambda: defaultdict(list))
    for component in conn.execute(
        """SELECT * FROM gold.financial_values
        WHERE calculation_run_id=%s AND company_id=ANY(%s) AND statement_type='income_statement'
        AND (line_item_path IN ('net dönem karı (zararı)','gelir ve gider kalemleri > net dönem karı (zararı)',
            'kontrol gücü olmayan paylar','azınlık payları karı (zararı)',
            'distribution of profit (loss) for the period > kontrol gücü olmayan paylar',
            'gelir ve gider kalemleri > azınlık payları karı (zararı)',
            'gelir ve gider kalemleri > grubun karı (zararı)')
          OR (item='ana ortaklık payları' AND starts_with(line_item_path,'distribution of profit (loss) for the period >')))""",
        (run_id, missing_companies),
    ):
        key = (
            component["company_id"],
            component["fiscal_year"],
            component["fiscal_quarter"],
        )
        role = (
            "net"
            if component["item"] == "net dönem karı (zararı)"
            else "parent"
            if component["item"] in ("ana ortaklık payları", "grubun karı (zararı)")
            else "minority"
        )
        components[key][role].append(component)
    for key, parts in components.items():
        if key in profits or parts["parent"]:
            continue
        if len(parts["net"]) != 1 or len(parts["minority"]) != 1:
            continue
        net, minority = parts["net"][0], parts["minority"][0]
        compatible = all(
            net[k] == minority[k]
            for k in (
                "period_start",
                "period_end",
                "measurement_basis",
                "purchasing_power_date",
            )
        )
        same_filing = set(
            net["calculation_details"].get("source_filing_keys", [])
        ) & set(minority["calculation_details"].get("source_filing_keys", []))
        if (
            not compatible
            or not same_filing
            or net["value_ytd"] is None
            or minority["value_ytd"] is None
        ):
            continue
        profits[key] = {
            **net,
            "value_ytd": net["value_ytd"] - minority["value_ytd"],
            "value_quarter": None
            if net["value_quarter"] is None or minority["value_quarter"] is None
            else net["value_quarter"] - minority["value_quarter"],
            "profit_reconstruction": {
                "method": "net_profit_less_disclosed_minority_profit",
                "net_financial_value_id": net["financial_value_id"],
                "minority_financial_value_id": minority["financial_value_id"],
            },
        }
    fingerprints = {
        company: hashlib.sha256(
            "\n".join(
                f"{r['price_date']}:{r['capital']}" for r in history.rows
            ).encode()
        ).hexdigest()
        for company, history in capital.items()
    }
    eligible = {
        r["company_id"]
        for r in conn.execute("""SELECT c.company_id FROM metadata.companies c
        LEFT JOIN metadata.instruments i USING(company_id)
        GROUP BY c.company_id HAVING count(i.instrument_id)<=1""")
    }
    paid_capital = {
        (r["company_id"], r["fiscal_year"], r["fiscal_quarter"]): r
        for r in conn.execute(
            """
        SELECT company_id,fiscal_year,fiscal_quarter,min(value_quarter) capital,
            min(period_end) period_end,min(financial_value_id) source_id
        FROM gold.financial_values WHERE calculation_run_id=%s AND statement_type='balance_sheet'
          AND item='ödenmiş sermaye'
        GROUP BY company_id,fiscal_year,fiscal_quarter
        HAVING count(DISTINCT value_quarter)=1 AND min(value_quarter)>0 AND count(DISTINCT period_end)=1""",
            (run_id,),
        )
    }
    counts = defaultdict(int)
    for row in rows:
        key = (row["company_id"], row["fiscal_year"], row["fiscal_quarter"])
        fact, history = profits.get(key), capital.get(row["company_id"])
        estimates, evidence, statuses = {}, {}, {}
        for kind in ("quarter", "ytd", "ttm"):
            value = None
            reason = "missing_or_ambiguous_parent_attributable_profit"
            if fact is not None and row["company_id"] in eligible:
                start, end, amount = (
                    fact["period_start"],
                    fact["period_end"],
                    fact["value_quarter"],
                )
                reason = "missing_period_profit_or_dates"
                if kind == "ytd" or (kind == "ttm" and row["fiscal_quarter"] == 4):
                    days = fact["calculation_details"].get("ytd_days")
                    start = end - timedelta(days=days - 1) if end and days else None
                    amount = fact["value_ytd"]
                    if kind == "ttm" and days not in (365, 366):
                        amount = None
                elif kind == "ttm":
                    ordinal = row["fiscal_year"] * 4 + row["fiscal_quarter"]
                    window = [
                        profits.get((row["company_id"], (p - 1) // 4, (p - 1) % 4 + 1))
                        for p in range(ordinal - 3, ordinal + 1)
                    ]
                    amount = None
                    reason = "incomplete_or_incompatible_trailing_profit_history"
                    if all(
                        r
                        and r["value_quarter"] is not None
                        and r["period_start"]
                        and r["period_end"]
                        for r in window
                    ):
                        evidence["ttm_profit_financial_value_ids"] = [
                            r["financial_value_id"] for r in window
                        ]
                        start = window[0]["period_start"]
                        if (end - start).days in (364, 365) and all(
                            b["period_start"] == a["period_end"] + timedelta(days=1)
                            for a, b in zip(window, window[1:])
                        ):
                            if all(r["measurement_basis"] == "nominal" for r in window):
                                amount = sum(r["value_quarter"] for r in window)
                            elif all(
                                r["measurement_basis"] == "inflation_adjusted"
                                for r in window
                            ):
                                factors = [
                                    cpi.factor(
                                        r["purchasing_power_date"],
                                        fact["purchasing_power_date"],
                                    )
                                    for r in window
                                ]
                                if all(f is not None for f in factors):
                                    amount = sum(
                                        r["value_quarter"] * f
                                        for r, f in zip(window, factors)
                                    )
                                    evidence["ttm_cpi_factors"] = [
                                        str(f) for f in factors
                                    ]
                if amount is not None and start and end:
                    denominator = (
                        history.period(start, end, allow_opening_estimate=True)
                        if history
                        else None
                    )
                    reason = "missing_or_stale_historical_capital"
                    fallback = paid_capital.get(key)
                    if (
                        denominator is None
                        and fallback
                        and fallback["period_end"] == end
                    ):
                        denominator = {
                            "weighted_capital": fallback["capital"],
                            "regimes": [],
                            "opening_assumption": None,
                            "method": "period_end_reported_paid_in_capital_proxy",
                            "paid_capital_financial_value_id": fallback["source_id"],
                        }
                    if denominator:
                        value = amount / denominator["weighted_capital"]
                        reason = None
                        evidence[kind] = {
                            "period_start": str(start),
                            "period_end": str(end),
                            "parent_profit": str(amount),
                            "weighted_issued_nominal_capital": str(
                                denominator["weighted_capital"]
                            ),
                            "capital_regimes": denominator["regimes"],
                            "profit_financial_value_id": fact["financial_value_id"],
                            "parent_profit_reconstruction": fact.get(
                                "profit_reconstruction"
                            ),
                            "opening_capital_assumption": denominator[
                                "opening_assumption"
                            ],
                            "denominator_method": denominator.get(
                                "method", "calendar_weighted_historical_issued_capital"
                            ),
                            "paid_capital_financial_value_id": denominator.get(
                                "paid_capital_financial_value_id"
                            ),
                        }
            elif fact is not None:
                reason = "missing_capital_or_multiple_instruments"
            estimates[kind] = value
            statuses[kind] = {
                "status": "verified"
                if row["value_" + kind] is not None
                else "estimated"
                if value is not None
                else "review_required",
                "unavailable_reason": None
                if row["value_" + kind] is not None
                else reason,
            }
            counts[kind + "_" + statuses[kind]["status"]] += 1
        assessment = {
            "unit": "TRY_per_1_TRY_nominal",
            "method": "parent_profit_over_historical_capital_with_explicit_proxy_fallback",
            "quality": statuses,
            "assumptions": [
                "Treasury shares are not deducted from issued capital.",
                "Capital changes have not been classified as cash, bonus or rights issues.",
                "Ordinary earnings rights must be reviewed; issuer-specific preference adjustments are not inferred.",
                "Up to seven opening calendar days can use the first observed historical capital; such cases are flagged in evidence.",
                "When historical prices are unavailable, reported period-end paid-in capital is a proxy for the weighted nominal denominator; its nominal legal interpretation and within-period changes are unverified.",
            ],
            "capital_source": "IsYatirim.SERMAYE",
            "capital_history_sha256": fingerprints.get(row["company_id"]),
            "evidence": evidence,
            "review_required": any(
                s["status"] == "review_required" for s in statuses.values()
            ),
        }
        conn.execute(
            """INSERT INTO gold.eps_coverage
            (calculation_run_id,company_id,fiscal_year,fiscal_quarter,estimated_value_quarter,estimated_value_ytd,estimated_value_ttm,assessment)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(calculation_run_id,company_id,fiscal_year,fiscal_quarter)
            DO UPDATE SET estimated_value_quarter=EXCLUDED.estimated_value_quarter,estimated_value_ytd=EXCLUDED.estimated_value_ytd,
              estimated_value_ttm=EXCLUDED.estimated_value_ttm,assessment=EXCLUDED.assessment""",
            (
                run_id,
                *key,
                estimates["quarter"],
                estimates["ytd"],
                estimates["ttm"],
                Jsonb(assessment),
            ),
        )
    return dict(counts)
