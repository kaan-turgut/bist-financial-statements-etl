"""Dated nominal-capital arithmetic; estimates never become verified EPS."""

from bisect import bisect_right
from datetime import timedelta
from decimal import Decimal


class CapitalHistory:
    def __init__(self, rows):
        self.rows = []
        for row in sorted(rows, key=lambda r: r["price_date"]):
            value = Decimal(str(row["capital"])) if row["capital"] is not None else None
            self.rows.append(
                {
                    **row,
                    "capital": value
                    if value is not None and value.is_finite()
                    else None,
                }
            )
        self.dates = [r["price_date"] for r in self.rows]
        self.regimes = []
        regime, previous = None, None
        for row in self.rows:
            capital = row["capital"]
            if capital != previous:
                regime = f"{row['price_date']}:{capital}"
            self.regimes.append(regime)
            previous = capital

    def period(self, start, end, *, allow_opening_estimate=False):
        """Calendar-day weighting, carrying trading observations at most 10 days."""
        if start is None or end is None or end < start:
            return None
        index = bisect_right(self.dates, start) - 1
        opening_assumption = None
        if index < 0:
            if (
                not allow_opening_estimate
                or not self.dates
                or not 0 < (self.dates[0] - start).days <= 7
                or self.dates[0] > end
            ):
                return None
            index = 0
            opening_assumption = {
                "method": "first_observed_capital_carried_back_to_period_open",
                "observed_date": str(self.dates[0]),
                "assumed_from": str(start),
            }
        cursor, total, regimes = start, Decimal(0), set()
        while cursor <= end:
            row = self.rows[index]
            stop = min(
                end + timedelta(days=1),
                self.dates[index + 1]
                if index + 1 < len(self.dates)
                else end + timedelta(days=1),
            )
            if (
                row["capital"] is None
                or row["capital"] <= 0
                or (stop - timedelta(days=1) - row["price_date"]).days > 10
            ):
                return None
            total += row["capital"] * (stop - cursor).days
            regimes.add(self.regimes[index])
            cursor = stop
            index += 1
        return {
            "weighted_capital": total / Decimal((end - start).days + 1),
            "stable_basis": next(iter(regimes)) if len(regimes) == 1 else None,
            "regimes": sorted(regimes),
            "opening_assumption": opening_assumption,
        }


def verified_share_basis(observation, capital):
    """A constant issued-capital regime rules out unhandled bonus-capital changes.

    The observation itself must disclose the actual outstanding denominator;
    price-feed capital is never substituted for that verified count.
    """
    if observation.share_basis_id or not observation.has_inputs or capital is None:
        return observation
    period = capital.period(observation.period_start, observation.period_end)
    if not period or not period["stable_basis"]:
        return observation
    presentation_quantum = Decimal(
        observation.normalization.get("shares_multiplier", "1")
    ) * Decimal(observation.normalization.get("share_nominal_value", "1"))
    if (
        observation.weighted_average_shares
        > period["weighted_capital"] + presentation_quantum
    ):
        return observation
    from dataclasses import replace

    return replace(
        observation,
        share_basis_id="verified_nominal_regime:" + period["stable_basis"],
        normalization={
            **observation.normalization,
            "share_basis_evidence": {
                "method": "disclosed_outstanding_denominator_with_stable_issued_capital",
                "period_start": str(observation.period_start),
                "period_end": str(observation.period_end),
                "issued_capital": str(period["weighted_capital"]),
            },
        },
    )
