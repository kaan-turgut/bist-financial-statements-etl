-- Retrospective quarter-end valuation, with reproducible price/EPS provenance.
CREATE TABLE gold.price_earnings_values (
    valuation_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    financial_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    instrument_id bigint REFERENCES metadata.instruments(instrument_id),
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    period_end date,
    price_date date,
    original_close_try numeric(28,12),
    basic_eps_ttm numeric(28,12),
    pe_ratio numeric(28,10),
    price_row_id bigint REFERENCES bronze.price_observations(price_row_id),
    calculation_details jsonb NOT NULL,
    PRIMARY KEY (valuation_run_id,company_id,fiscal_year,fiscal_quarter),
    CHECK (price_date IS NULL OR price_date<=period_end),
    CHECK (pe_ratio IS NULL OR (pe_ratio>0 AND basic_eps_ttm>0 AND original_close_try>0))
);
CREATE INDEX price_earnings_financial_period_idx ON gold.price_earnings_values
    (financial_run_id,company_id,fiscal_year,fiscal_quarter,valuation_run_id DESC);
CREATE VIEW gold.current_price_earnings AS
SELECT DISTINCT ON (v.company_id,v.fiscal_year,v.fiscal_quarter) v.*
FROM gold.price_earnings_values v JOIN ops.active_company_versions a
 ON a.dataset_name='financials' AND a.company_id=v.company_id AND a.run_id=v.financial_run_id
ORDER BY v.company_id,v.fiscal_year,v.fiscal_quarter,v.valuation_run_id DESC;
