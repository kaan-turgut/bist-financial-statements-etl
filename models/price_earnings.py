"""Retrospective P/E with separate verified and capital-based estimated values."""

from datetime import date
from decimal import Decimal, InvalidOperation

MAX_PRICE_AGE_DAYS = 10


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def purchasing_power_matches(details, sources, end):
    if all(s.get("purchasing_power_date") in (None, end) for s in sources):
        return True
    if details.get("ttm_method") not in {
        "verified_four_quarters_cpi",
        "verified_annual_ytd_bridge_cpi",
    } or details.get("purchasing_power_date_ttm") != str(end):
        return False
    # A derived quarter may already convert its previous YTD numerator into
    # that quarter's power before the TTM conversion. Retain both steps.
    reachable = {str(end)}
    conversions = details.get("cpi_conversions") or []
    while True:
        earlier = {
            c.get("source_date")
            for c in conversions
            if c.get("target_date") in reachable
            and number(c.get("factor")) is not None
            and number(c.get("factor")) > 0
        }
        if earlier <= reachable:
            break
        reachable |= earlier
    return all(
        s.get("purchasing_power_date") is not None
        and str(s["purchasing_power_date"]) in reachable
        for s in sources
    )


def estimated_price_earnings(row):
    """Capital-based coverage estimates never replace a disclosed loss or EPS."""
    eps = number(row.get("estimated_eps_ttm"))
    price = number((row.get("source_payload") or {}).get("at_the_date_close_price"))
    assessment = row.get("eps_assessment") or {}
    evidence = assessment.get("evidence", {}).get("ttm", {})
    end, day = row.get("period_end"), row.get("price_date")
    try:
        estimate_days = (
            date.fromisoformat(evidence["period_end"])
            - date.fromisoformat(evidence["period_start"])
        ).days + 1
    except (KeyError, TypeError, ValueError):
        estimate_days = None
    reason = None
    if row.get("value_ttm") is not None:
        reason = "verified_eps_available"
    elif eps is None:
        reason = "missing_estimated_ttm_eps"
    elif eps <= 0:
        reason = "nonpositive_estimated_ttm_eps"
    elif end is None or row.get("period_end_count", 1) != 1:
        reason = "missing_or_ambiguous_fiscal_period_end"
    elif not row.get("instrument_id") or row.get("currency") != "TRY":
        reason = "missing_try_instrument"
    elif row.get("instrument_count", 1) != 1:
        reason = "multiple_share_classes_require_class_specific_eps"
    elif evidence.get("period_end") != str(end) or estimate_days not in {365, 366}:
        reason = "missing_matching_estimate_evidence"
    elif evidence.get("measurement_basis") not in {"nominal", "inflation_adjusted"} or (
        evidence.get("measurement_basis") == "inflation_adjusted"
        and evidence.get("purchasing_power_date") != str(end)
    ):
        reason = "unsupported_estimate_purchasing_power_basis"
    elif day is None:
        reason = "no_price_near_fiscal_period_end"
    elif not 0 <= (end - day).days <= MAX_PRICE_AGE_DAYS:
        reason = "future_or_stale_price"
    elif price is None or price <= 0:
        reason = "missing_or_invalid_original_close"
    return {
        "estimated_eps_ttm": eps,
        "estimated_pe_ratio": None if reason else price / eps,
        "details": {
            "method": "original_close_divided_by_historical_capital_eps_estimate",
            "unavailable_reason": reason,
            "evidence": evidence,
            "ttm_cpi_factors": assessment.get("evidence", {}).get("ttm_cpi_factors"),
            "ttm_profit_financial_value_ids": assessment.get("evidence", {}).get(
                "ttm_profit_financial_value_ids"
            ),
            "assumptions": assessment.get("assumptions", []),
            "capital_history_sha256": assessment.get("capital_history_sha256"),
        },
    }


def price_earnings(row, sources):
    """No annualized quarterly EPS, future prices, or adjusted-price fallback."""
    eps = number(row["value_ttm"])
    price = number((row.get("source_payload") or {}).get("at_the_date_close_price"))
    details = {
        "method": "original_quarter_end_close_divided_by_basic_ttm_eps",
        "price_policy": "last_available_trading_close_on_or_before_actual_fiscal_period_end",
        "max_price_age_days": MAX_PRICE_AGE_DAYS,
        "price_field": "IsYatirim.HG_KAPANIS",
        "eps_unit": "TRY_per_1_TRY_nominal",
        "eps_source_ids": row["calculation_details"].get(
            "ttm_source_ids", row["calculation_details"].get("source_ids", [])
        ),
        "eps_ttm_method": row["calculation_details"].get("ttm_method"),
        "retrospective": True,
        "publication_aware": False,
    }
    reason = None
    end, price_date = row.get("period_end"), row.get("price_date")
    if eps is None:
        reason = "missing_verified_ttm_eps"
    elif eps <= 0:
        reason = "nonpositive_ttm_eps"
    elif end is None or row.get("period_end_count", 1) != 1:
        reason = "missing_or_ambiguous_fiscal_period_end"
    elif not row.get("instrument_id") or row.get("currency") != "TRY":
        reason = "missing_try_instrument"
    elif row.get("instrument_count", 1) != 1:
        reason = "multiple_share_classes_require_class_specific_eps"
    elif row["calculation_details"].get("unit") != "TRY_per_1_TRY_nominal":
        reason = "unsupported_eps_unit"
    elif (
        not sources
        or not any(s["period_end"] == end for s in sources)
        or set(details["eps_source_ids"]) != {s["source_id"] for s in sources}
    ):
        reason = "missing_matching_eps_source"
    elif any(
        number(s["normalization"].get("split_adjustment_factor")) != 1 for s in sources
    ):
        reason = "eps_split_basis_requires_price_reconciliation"
    elif not purchasing_power_matches(row["calculation_details"], sources, end):
        reason = "eps_purchasing_power_date_differs_from_price_period"
    elif price_date is None:
        reason = "no_price_near_fiscal_period_end"
    elif not 0 <= (end - price_date).days <= MAX_PRICE_AGE_DAYS:
        reason = "future_or_stale_price"
    elif price is None or price <= 0:
        reason = "missing_or_invalid_original_close"
    details["unavailable_reason"] = reason
    return {
        "original_close_try": price,
        "basic_eps_ttm": eps,
        "pe_ratio": None if reason else price / eps,
        "calculation_details": details,
    }
