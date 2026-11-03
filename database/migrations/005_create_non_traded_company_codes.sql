
CREATE TABLE IF NOT EXISTS metadata.non_traded_company_codes (

    -- Unique KAP company code
    symbol TEXT PRIMARY KEY,

    -- KAP company information
    kap_summary_url TEXT NOT NULL,
    kap_general_url TEXT NOT NULL,

    -- Last verification timestamp
    last_checked_at TIMESTAMPTZ
);
