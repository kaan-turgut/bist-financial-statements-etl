"""Verified EPS inputs normalized to TRY per 1 TRY nominal share capital."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise


def decimal_value(value):
    if value is None:
        return None
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError("EPS inputs must be finite numbers.")
    return value


@dataclass(frozen=True)
class EpsObservation:
    period_start: date
    period_end: date
    period_kind: str
    eps_kind: str = "basic"
    share_class: str = "ordinary"
    earnings_scope: str = "total"
    currency: str = "TRY"
    reported_eps: Decimal | None = None
    attributable_profit: Decimal | None = None
    weighted_average_shares: Decimal | None = None
    share_basis_id: str | None = None
    purchasing_power_date: date | None = None
    rounding_tolerance: Decimal = Decimal("0.00000001")
    normalization: dict = field(default_factory=dict)
    source_id: int | None = None

    def __post_init__(self):
        for name in (
            "reported_eps",
            "attributable_profit",
            "weighted_average_shares",
            "rounding_tolerance",
        ):
            object.__setattr__(self, name, decimal_value(getattr(self, name)))
        if self.period_end < self.period_start or self.period_kind not in {
            "quarter",
            "ytd",
        }:
            raise ValueError("Invalid EPS reporting period.")
        if self.eps_kind not in {"basic", "diluted"} or self.earnings_scope not in {
            "total",
            "continuing",
        }:
            raise ValueError("Invalid EPS kind or earnings scope.")
        if self.currency != "TRY" or not self.share_class:
            raise ValueError(
                "EPS requires an explicit share class and TRY reporting currency."
            )
        if self.share_basis_id is not None and (
            not isinstance(self.share_basis_id, str) or not self.share_basis_id.strip()
        ):
            raise ValueError("An EPS share basis identifier must not be empty.")
        if self.period_kind == "quarter" and not 75 <= self.days <= 100:
            raise ValueError("A quarterly EPS observation must cover one quarter.")
        if (
            self.weighted_average_shares is not None
            and self.weighted_average_shares <= 0
        ):
            raise ValueError("Weighted-average shares must be positive.")
        if self.rounding_tolerance < 0:
            raise ValueError("EPS rounding tolerance must not be negative.")
        if self.reported_eps is None and not self.has_inputs:
            raise ValueError(
                "EPS requires a reported value or verified earnings and shares."
            )
        if self.reported_eps is not None and self.has_inputs:
            calculated = self.attributable_profit / self.weighted_average_shares
            if abs(calculated - self.reported_eps) > self.rounding_tolerance:
                raise ValueError(
                    "Reported EPS does not reconcile with attributable earnings and weighted-average shares."
                )

    @property
    def has_inputs(self):
        return (
            self.attributable_profit is not None
            and self.weighted_average_shares is not None
        )

    @property
    def value(self):
        if self.reported_eps is not None:
            return self.reported_eps
        return self.attributable_profit / self.weighted_average_shares

    @property
    def days(self):
        return (self.period_end - self.period_start).days + 1


def normalize_observation(
    *,
    reported_eps=None,
    attributable_profit=None,
    weighted_average_shares=None,
    eps_unit="TRY",
    money_multiplier=1,
    shares_multiplier=1,
    share_nominal_value=1,
    split_adjustment_factor=1,
    **kwargs,
):
    """Separate money, count, currency, nominal-value and split adjustments.

    Split factors and share_basis_id are supplied only from verified historical
    disclosures; current company metadata is never used to infer them.
    """
    if eps_unit not in {"TRY", "KURUS"}:
        raise ValueError("EPS unit must be TRY or KURUS.")
    scales = [
        decimal_value(v)
        for v in (
            money_multiplier,
            shares_multiplier,
            share_nominal_value,
            split_adjustment_factor,
        )
    ]
    if any(v is None or v <= 0 for v in scales):
        raise ValueError("EPS normalization factors must be positive.")
    money, shares, nominal, split = scales
    eps = decimal_value(reported_eps)
    profit = decimal_value(attributable_profit)
    count = decimal_value(weighted_average_shares)
    eps_scale = (
        (Decimal("0.01") if eps_unit == "KURUS" else Decimal(1)) / nominal / split
    )
    normalization = {
        "raw_reported_eps": str(eps) if eps is not None else None,
        "eps_unit": eps_unit,
        "money_multiplier": str(money),
        "shares_multiplier": str(shares),
        "share_nominal_value": str(nominal),
        "split_adjustment_factor": str(split),
        "output_unit": "TRY_per_1_TRY_nominal",
    }
    normalization.update(kwargs.pop("normalization", {}))
    if "rounding_tolerance" in kwargs:
        kwargs["rounding_tolerance"] = (
            decimal_value(kwargs["rounding_tolerance"]) * eps_scale
        )
    return EpsObservation(
        reported_eps=None if eps is None else eps * eps_scale,
        attributable_profit=None if profit is None else profit * money,
        weighted_average_shares=None
        if count is None
        else count * shares * nominal * split,
        normalization=normalization,
        **kwargs,
    )


def _compatible(left, right, cpi=None):
    return (
        left.share_basis_id is not None
        and left.share_basis_id == right.share_basis_id
        and left.currency == right.currency
        and (
            left.purchasing_power_date == right.purchasing_power_date
            or (
                cpi is not None
                and left.purchasing_power_date is not None
                and right.purchasing_power_date is not None
                and cpi.factor(right.purchasing_power_date, left.purchasing_power_date)
                is not None
            )
        )
    )


def profit_at(observation, target, cpi):
    if observation.purchasing_power_date == target:
        return observation.attributable_profit
    factor = cpi.factor(observation.purchasing_power_date, target) if cpi else None
    return None if factor is None else observation.attributable_profit * factor


def calculate_history(history, cpi=None):
    """Return EPS per (fiscal year, quarter, kind, class, scope), without proxies."""
    groups = {
        (o.eps_kind, o.share_class, o.earnings_scope)
        for rows in history.values()
        for o in rows
    }
    result = {}
    quarters = {}
    for year, quarter in sorted(history):
        observations = history[(year, quarter)]
        for group in groups:
            selected = {
                o.period_kind: o
                for o in observations
                if (o.eps_kind, o.share_class, o.earnings_scope) == group
            }
            ytd = selected.get("ytd")
            current = selected.get("quarter") or (
                ytd if quarter == 1 and ytd is not None and ytd.days <= 100 else None
            )
            previous_ytd = next(
                (
                    o
                    for o in history.get((year, quarter - 1), [])
                    if o.period_kind == "ytd"
                    and (o.eps_kind, o.share_class, o.earnings_scope) == group
                ),
                None,
            )
            if (
                quarter > 1
                and ytd
                and previous_ytd
                and ytd.has_inputs
                and previous_ytd.has_inputs
                and _compatible(ytd, previous_ytd, cpi)
                and ytd.period_start == previous_ytd.period_start
            ):
                days = (ytd.period_end - previous_ytd.period_end).days
                if 75 <= days <= 100:
                    shares = (
                        ytd.weighted_average_shares * ytd.days
                        - previous_ytd.weighted_average_shares * previous_ytd.days
                    ) / days
                    if shares > 0:
                        profit = ytd.attributable_profit - profit_at(
                            previous_ytd, ytd.purchasing_power_date, cpi
                        )
                        derived = EpsObservation(
                            period_start=previous_ytd.period_end + timedelta(days=1),
                            period_end=ytd.period_end,
                            period_kind="quarter",
                            eps_kind=group[0],
                            share_class=group[1],
                            earnings_scope=group[2],
                            attributable_profit=profit,
                            weighted_average_shares=shares,
                            share_basis_id=ytd.share_basis_id,
                            purchasing_power_date=ytd.purchasing_power_date,
                            normalization={
                                "method": "derived_from_ytd",
                                "source_ids": [previous_ytd.source_id, ytd.source_id],
                            },
                        )
                        if (
                            current is None
                            or abs(current.value - derived.value)
                            <= current.rounding_tolerance
                        ):
                            if current is not None:
                                derived = EpsObservation(
                                    **{
                                        **vars(derived),
                                        "reported_eps": current.reported_eps,
                                        "rounding_tolerance": current.rounding_tolerance,
                                        "source_id": current.source_id,
                                    }
                                )
                            current = derived
            key = (year, quarter, *group)
            quarters[key] = current
            detail = {
                "unit": "TRY_per_1_TRY_nominal",
                "quarter_method": None,
                "ytd_method": None,
                "ttm_method": None,
                "ttm_unavailable_reason": "insufficient_verified_quarterly_inputs",
                "source_ids": sorted(
                    {o.source_id for o in selected.values() if o.source_id is not None}
                ),
                "share_basis_evidence": [
                    o.normalization["share_basis_evidence"]
                    for o in selected.values()
                    if "share_basis_evidence" in o.normalization
                ],
            }
            detail["comparison_basis"] = {
                name: (
                    f"{o.share_basis_id}|{o.currency}|{o.purchasing_power_date}"
                    if o and o.share_basis_id
                    else None
                )
                for name, o in (("quarter", current), ("ytd", ytd))
            }
            detail["comparison_basis"]["ttm"] = None
            if current:
                detail["quarter_method"] = (
                    "reported" if current.reported_eps is not None else "calculated"
                )
                detail["source_ids"] = sorted(
                    set(detail["source_ids"])
                    | {
                        source
                        for source in current.normalization.get(
                            "source_ids", [current.source_id]
                        )
                        if source is not None
                    }
                )
            if ytd:
                detail["ytd_method"] = (
                    "reported" if ytd.reported_eps is not None else "calculated"
                )
            ttm = None
            if quarter == 4 and ytd and ytd.days in {365, 366}:
                ttm = ytd.value
                detail["ttm_method"] = "annual_report"
                detail["comparison_basis"]["ttm"] = detail["comparison_basis"]["ytd"]
            else:
                period = year * 4 + quarter
                window = [
                    quarters.get(((p - 1) // 4, (p - 1) % 4 + 1, *group))
                    for p in range(period - 3, period + 1)
                ]
                if all(o is not None and o.has_inputs for o in window):
                    if not all(_compatible(window[-1], o, cpi) for o in window):
                        detail["ttm_unavailable_reason"] = (
                            "unverified_or_incompatible_share_or_purchasing_power_basis"
                        )
                    elif sum(o.days for o in window) in {365, 366} and all(
                        (right.period_start - left.period_end).days == 1
                        for left, right in pairwise(window)
                    ):
                        days = sum(o.days for o in window)
                        weighted_shares = (
                            sum(o.weighted_average_shares * o.days for o in window)
                            / days
                        )
                        target = window[-1].purchasing_power_date
                        ttm = (
                            sum(profit_at(o, target, cpi) for o in window)
                            / weighted_shares
                        )
                        converted = any(
                            o.purchasing_power_date != target for o in window
                        )
                        detail.update(
                            ttm_method="verified_four_quarters_cpi"
                            if converted
                            else "verified_four_quarters",
                            weighted_average_shares_ttm=str(weighted_shares),
                            purchasing_power_date_ttm=str(target) if target else None,
                            cpi_conversions=[
                                {
                                    "source_date": str(o.purchasing_power_date),
                                    "target_date": str(target),
                                    "factor": str(
                                        cpi.factor(o.purchasing_power_date, target)
                                    ),
                                }
                                for o in window
                            ]
                            if converted
                            else [],
                        )
                        detail["comparison_basis"]["ttm"] = detail["comparison_basis"][
                            "quarter"
                        ]
                        detail["source_ids"] = sorted(
                            set(detail["source_ids"])
                            | {
                                source
                                for o in window
                                for source in o.normalization.get(
                                    "source_ids", [o.source_id]
                                )
                                if source is not None
                            }
                        )
            if ttm is not None:
                detail["ttm_unavailable_reason"] = None
            result[key] = {
                "value_quarter": current.value if current else None,
                "value_ytd": ytd.value if ytd else None,
                "value_ttm": ttm,
                "details": detail,
            }
    return result
