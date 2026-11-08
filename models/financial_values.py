"""Period arithmetic with explicit purchasing-power bases and complete CPI spans."""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise


def month_number(day):
    return day.year * 12 + day.month


class CpiSeries:
    def __init__(self, rows):
        self.rates = {
            month_number(r["observation_date"]): Decimal(r["value"]) for r in rows
        }

    def factor(self, source, target):
        if source is None or target is None:
            return None
        start, stop = month_number(source), month_number(target)
        # Even a same-month conversion requires the requested CPI observation.
        if start not in self.rates or stop not in self.rates:
            return None
        value = Decimal(1)
        for month in range(min(start, stop) + 1, max(start, stop) + 1):
            rate = self.rates.get(month)
            if rate is None or rate <= -100:
                return None
            value *= 1 + rate / 100
        return value if stop >= start else 1 / value


def _quarter_start(ytd_start, quarter):
    month = month_number(ytd_start) + 3 * (quarter - 1) - 1
    year, month = divmod(month, 12)
    return date(
        year, month + 1, min(ytd_start.day, calendar.monthrange(year, month + 1)[1])
    )


def _convert(amount, source, target, cpi):
    if source.get("report_kind") != target.get("report_kind") or source.get(
        "currency", "TRY"
    ) != target.get("currency", "TRY"):
        return None
    left, right = (
        source.get("measurement_basis", "unknown"),
        target.get("measurement_basis", "unknown"),
    )
    if left == right == "nominal":
        return amount
    if left == right == "inflation_adjusted":
        factor = cpi.factor(
            source.get("purchasing_power_date"), target.get("purchasing_power_date")
        )
        return None if factor is None else amount * factor
    return None


def calculate_values(observations, cpi, target_date, cash_balances):
    """Prefer reported figures; derive only from adjacent compatible periods.

    Each result's quarter and YTD are expressed in that report's source basis.
    value_real converts the quarter to the requested common CPI month. This
    analytic conversion does not recreate IAS 29 restatements of nominal books.
    """
    grouped = defaultdict(list)
    for row in observations:
        key = (
            row["company_id"],
            row["statement_type"],
            row["fiscal_year"],
            row["fiscal_quarter"],
            row["line_item_path"],
            row["item"],
            row["source_column_id"],
        )
        grouped[key].append(row)
    results, reported_ytd = {}, {}
    for key in sorted(
        grouped, key=lambda k: (k[0], k[2], k[3], k[1], k[4], k[5], str(k[6]))
    ):
        rows = grouped[key]
        stock = key[1] not in {"income_statement", "cash_flow"} or (
            key[1] == "cash_flow" and key[5] in cash_balances
        )
        quarter_rows = [r for r in rows if not r["is_cumulative"]]
        ytd_rows = [r for r in rows if r["is_cumulative"]]

        def choose(candidates, line_key=key):
            if not candidates:
                return None
            if (
                len(
                    {
                        (r["period_start"], r["period_end"], r["amount"])
                        for r in candidates
                    }
                )
                != 1
            ):
                raise ValueError(
                    f"Conflicting observations for financial line {line_key}"
                )
            return candidates[0]

        q, ytd = choose(quarter_rows), choose(ytd_rows)
        source = ytd or q
        current = dict(source)
        details = {
            "quarter_method": "reported" if q else None,
            "ytd_method": "reported" if ytd else None,
            "source_filing_keys": [source["filing_key"]],
        }
        if ytd:
            details["ytd_days"] = (ytd["period_end"] - ytd["period_start"]).days + 1
        qvalue = Decimal(q["amount"]) if q else None
        yvalue = Decimal(ytd["amount"]) if ytd else None
        quarter = key[3]
        if ytd:
            reported_ytd[key] = ytd
            current["period_start"] = _quarter_start(ytd["period_start"], quarter)
        if stock:
            qvalue = yvalue = Decimal(source["amount"])
            details.update(
                quarter_method="reported_balance", ytd_method="reported_balance"
            )
        elif quarter == 1:
            qvalue = qvalue if qvalue is not None else yvalue
            yvalue = yvalue if yvalue is not None else qvalue
            details.update(quarter_method="reported", ytd_method="reported")
        else:
            previous_key = (*key[:3], quarter - 1, *key[4:])
            previous = reported_ytd.get(previous_key)
            if (
                qvalue is None
                and ytd
                and previous
                and previous["period_start"] == ytd["period_start"]
            ):
                days = (ytd["period_end"] - previous["period_end"]).days
                if 75 <= days <= 100 and previous.get("report_kind") == ytd.get(
                    "report_kind"
                ):
                    preceding = _convert(
                        Decimal(previous["amount"]), previous, ytd, cpi
                    )
                    if preceding is not None:
                        qvalue = yvalue - preceding
                        current["period_start"] = previous["period_end"] + timedelta(
                            days=1
                        )
                        details.update(quarter_method="difference_of_compatible_ytd")
                        details["source_filing_keys"].append(previous["filing_key"])
            prior = [results.get((*key[:3], p, *key[4:])) for p in range(1, quarter)]
            contiguous = (
                all(r and r["value_quarter"] is not None for r in prior)
                and all(
                    (right["period_start"] - left["period_end"]).days == 1
                    for left, right in pairwise(prior)
                )
                and prior[-1]["period_end"] + timedelta(days=1)
                == current["period_start"]
            )
            if contiguous:
                amounts = [_convert(r["value_quarter"], r, source, cpi) for r in prior]
                if all(a is not None for a in amounts):
                    if (
                        qvalue is None
                        and yvalue is not None
                        and prior[0]["period_start"] == ytd["period_start"]
                    ):
                        qvalue = yvalue - sum(amounts)
                        details["quarter_method"] = "ytd_less_compatible_quarters"
                    if yvalue is None and qvalue is not None:
                        yvalue = sum(amounts) + qvalue
                        details["ytd_method"] = "sum_of_compatible_quarters"
                    details["source_filing_keys"].extend(r["filing_key"] for r in prior)
        basis = source.get("measurement_basis", "unknown")
        price_date = (
            source.get("purchasing_power_date")
            if basis == "inflation_adjusted"
            else source["period_end"]
        )
        factor = cpi.factor(price_date, target_date) if basis != "unknown" else None
        current.update(
            value_quarter=qvalue,
            value_ytd=yvalue,
            value_real=None if qvalue is None or factor is None else qvalue * factor,
            value_real_ytd=None
            if yvalue is None or factor is None
            else yvalue * factor,
            measurement_basis=basis,
            calculation_details=details,
        )
        if qvalue is None:
            details["quarter_unavailable_reason"] = (
                "missing_or_incompatible_prior_period"
            )
        if factor is None:
            details["real_unavailable_reason"] = (
                "unknown_measurement_basis_or_incomplete_cpi"
            )
        results[key] = current
    return list(results.values())
