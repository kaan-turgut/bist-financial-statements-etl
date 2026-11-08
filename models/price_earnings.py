"""Conservative retrospective P/E using original prices and verified TTM EPS."""

from decimal import Decimal, InvalidOperation

MAX_PRICE_AGE_DAYS = 10


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


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
        "eps_source_ids": row["calculation_details"].get("source_ids", []),
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
    elif any(
        s.get("purchasing_power_date") not in (None, end) for s in sources
    ) and not (
        row["calculation_details"].get("ttm_method") == "verified_four_quarters_cpi"
        and row["calculation_details"].get("purchasing_power_date_ttm") == str(end)
        and row["calculation_details"].get("cpi_conversions")
        and all(
            any(
                conversion.get("source_date") == str(s.get("purchasing_power_date"))
                and conversion.get("target_date") == str(end)
                and number(conversion.get("factor")) is not None
                and number(conversion.get("factor")) > 0
                for conversion in row["calculation_details"]["cpi_conversions"]
            )
            for s in sources
        )
    ):
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
