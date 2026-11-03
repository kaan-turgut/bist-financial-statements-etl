CREATE TABLE IF NOT EXISTS metadata.share_info (

	-- Relationship to the parent company
	company_id BIGINT NOT NULL,

	symbol TEXT NOT NULL,

	-- Share quantities
	total_shares NUMERIC (24,4),
	non_traded_shares NUMERIC (24,4),
	traded_shares NUMERIC (24,4),

	-- Shares excluded from free-float calculation
	excluded_traded_shares_ic NUMERIC (24,4),
	excluded_traded_shares_ide NUMERIC (24,4),

	-- Free-float information
	free_float_shares NUMERIC (24,4),
	free_float_ratio NUMERIC (7,4),

	-- Constraints
	PRIMARY KEY(company_id, symbol),

	CONSTRAINT uq_symbol
		UNIQUE(symbol),

	CONSTRAINT fk_company_id
		FOREIGN KEY(company_id)
		REFERENCES metadata.companies(company_id)
		ON DELETE CASCADE
)