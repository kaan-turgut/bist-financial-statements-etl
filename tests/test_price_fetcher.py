import services.price_fetcher as prices


def price_row(symbol, day, close):
    return {
        "HGDG_HS_KODU": symbol,
        "HGDG_TARIH": day,
        "HGDG_KAPANIS": close,
    }


def test_prices_to_frame_normalizes_dates_and_deduplicates():
    frame = prices.prices_to_frame(
        [
            price_row("thyao", "01-01-2025", 10),
            price_row("THYAO", "01-01-2025", 11),
        ]
    )

    assert len(frame) == 1
    assert frame.iloc[0]["symbol"] == "THYAO"
    assert frame.iloc[0]["date"] == "2025-01-01"
    assert frame.iloc[0]["close_price"] == 11


