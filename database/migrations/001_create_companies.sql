CREATE SCHEMA IF NOT EXISTS metadata;

CREATE TABLE IF NOT EXISTS metadata.companies (
    -- Primary key
    company_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Company identifiers
    symbol TEXT NOT NULL UNIQUE,
    mkk_member_oid TEXT NOT NULL UNIQUE,
    company_code TEXT,
    stock_code TEXT,
    free_float_symbol TEXT,
    abcd_code TEXT,

    -- Company information
    kap_member_title TEXT,
    related_member_title TEXT,
    related_member_oid TEXT,
    city_name TEXT,
    kap_member_type TEXT,
    kap_member_state TEXT,
    financial_type TEXT,
    activity_state TEXT,
    trading_state TEXT,

    -- Registration and tax information
    tax_no TEXT,
    tax_office TEXT,
    trade_reg_no TEXT,
    trade_reg_office TEXT,
    trade_reg_date TIMESTAMP,

    -- Market and instrument information
    bist_market TEXT,
    instrument_type TEXT,
    instrument_listing_date DATE,
    instrument_country TEXT,
    instrument_exchange TEXT,
    instrument_market TEXT,

    -- Business information
    website TEXT,
    activity_field TEXT,
    activity_status_text TEXT,
    independent_audit_company TEXT,

    -- Capital and share information
    paid_in_capital NUMERIC(20,2),
    capital_ceiling NUMERIC(20,2),
    free_float_shares NUMERIC(24,4),
    free_float_ratio NUMERIC(7,4),
    shares_outstanding NUMERIC(24,4),
    has_traded_shares BOOLEAN,
    capital_details JSONB,

    -- Source information
    kap_summary_url TEXT,
    kap_general_url TEXT,
    source TEXT,
    source_updated_at TIMESTAMPTZ,
    last_scraped_at TIMESTAMPTZ
);