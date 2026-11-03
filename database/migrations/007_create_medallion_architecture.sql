-- Financial pipeline medallion foundation. Apply after migrations 001-006.
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS metadata.instruments (
    instrument_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id) ON DELETE CASCADE,
    symbol text NOT NULL UNIQUE,
    share_class text,
    exchange text NOT NULL DEFAULT 'BIST',
    currency char(3) NOT NULL DEFAULT 'TRY',
    is_primary boolean NOT NULL DEFAULT false,
    valid_from date,
    valid_to date,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS instruments_one_primary_per_company_idx
    ON metadata.instruments (company_id) WHERE is_primary;

CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pipeline_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','succeeded','failed')),
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error_text text
);

CREATE TABLE IF NOT EXISTS ops.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bronze.statement_filings (
    filing_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    filing_id bigint,
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    statement_type text NOT NULL,
    report_kind text,
    source_filename text NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    run_id bigint REFERENCES ops.pipeline_runs(run_id),
    UNIQUE (company_id, fiscal_year, fiscal_quarter, statement_type, source_sha256)
);

CREATE TABLE IF NOT EXISTS bronze.statement_rows (
    bronze_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_key bigint NOT NULL REFERENCES bronze.statement_filings(filing_key) ON DELETE CASCADE,
    source_row_number integer NOT NULL,
    source_row_id text,
    source_column_id text,
    column_label text,
    source_date text,
    section text,
    line_item_path text,
    item text NOT NULL,
    raw_value numeric(28,6),
    reporting_unit numeric(28,6),
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (filing_key, source_row_number)
);

CREATE TABLE IF NOT EXISTS silver.financial_observations (
    observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    filing_key bigint NOT NULL REFERENCES bronze.statement_filings(filing_key),
    processing_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    statement_type text NOT NULL,
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    period_start date,
    period_end date,
    section text,
    subsection text,
    line_item_path text NOT NULL,
    item text NOT NULL,
    amount numeric(28,6) NOT NULL,
    currency char(3) NOT NULL DEFAULT 'TRY',
    is_cumulative boolean NOT NULL DEFAULT false,
    report_kind text,
    source_row_id text,
    source_column_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT
        (processing_run_id, filing_key, source_row_id, source_column_id, line_item_path, item)
);

CREATE TABLE IF NOT EXISTS ops.active_filing_versions (
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    statement_type text NOT NULL,
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    filing_key bigint NOT NULL REFERENCES bronze.statement_filings(filing_key),
    processing_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    activated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, statement_type, fiscal_year, fiscal_quarter)
);

CREATE OR REPLACE VIEW silver.current_financial_observations AS
SELECT s.* FROM silver.financial_observations s
JOIN ops.active_filing_versions a
  ON a.company_id=s.company_id AND a.statement_type=s.statement_type
 AND a.fiscal_year=s.fiscal_year AND a.fiscal_quarter=s.fiscal_quarter
 AND a.filing_key=s.filing_key AND a.processing_run_id=s.processing_run_id;

CREATE INDEX IF NOT EXISTS financial_observations_period_idx
    ON silver.financial_observations (company_id, statement_type, fiscal_year, fiscal_quarter);
CREATE INDEX IF NOT EXISTS financial_observations_line_idx
    ON silver.financial_observations (company_id, line_item_path, fiscal_year, fiscal_quarter);

CREATE TABLE IF NOT EXISTS bronze.macro_observations (
    macro_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    series_code text NOT NULL,
    observation_date date NOT NULL,
    raw_value text NOT NULL,
    source text NOT NULL,
    source_hash text NOT NULL,
    run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    source_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (series_code, observation_date, source, source_hash)
);

CREATE TABLE IF NOT EXISTS silver.monthly_macro_data (
    series_code text NOT NULL,
    year integer NOT NULL,
    month smallint NOT NULL CHECK (month BETWEEN 1 AND 12),
    observation_date date NOT NULL,
    value numeric(20,8) NOT NULL,
    source text NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (series_code, observation_date)
);

CREATE TABLE IF NOT EXISTS bronze.price_observations (
    price_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id bigint NOT NULL REFERENCES metadata.instruments(instrument_id),
    price_date date NOT NULL,
    open numeric(20,6), high numeric(20,6), low numeric(20,6), close numeric(20,6),
    volume numeric(28,6), capital numeric(28,6), source text NOT NULL,
    source_hash text NOT NULL,
    run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    source_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (instrument_id, price_date, source, source_hash)
);

CREATE TABLE IF NOT EXISTS silver.daily_prices (
    instrument_id bigint NOT NULL REFERENCES metadata.instruments(instrument_id),
    price_date date NOT NULL,
    open numeric(20,6), high numeric(20,6), low numeric(20,6), close numeric(20,6),
    volume numeric(28,6), capital numeric(28,6), source text NOT NULL,
    PRIMARY KEY (instrument_id, price_date, source)
);

CREATE TABLE IF NOT EXISTS gold.financial_values (
    financial_value_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    calculation_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    statement_type text NOT NULL,
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    line_item_path text NOT NULL,
    item text NOT NULL,
    source_column_id text,
    column_label text,
    value_quarter numeric(28,6),
    value_ytd numeric(28,6),
    value_ttm numeric(28,6),
    value_real numeric(28,6),
    value_real_ttm numeric(28,6),
    value_real_yoy_change numeric(28,10),
    value_real_qoq_change numeric(28,10),
    value_real_ttm_qoq_change numeric(28,10),
    value_usd numeric(28,6),
    inflation_base_year integer,
    inflation_base_month smallint,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT
        (calculation_run_id, company_id, statement_type, fiscal_year, fiscal_quarter,
         line_item_path, item, source_column_id)
);

CREATE TABLE IF NOT EXISTS gold.financial_ratios (
    calculation_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    ratio_code text NOT NULL,
    value_quarter numeric(28,10),
    value_ytd numeric(28,10),
    value_ttm numeric(28,10),
    quarter_yoy_change numeric(28,10),
    quarter_qoq_change numeric(28,10),
    ytd_yoy_change numeric(28,10),
    ttm_qoq_change numeric(28,10),
    calculated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (calculation_run_id, company_id, fiscal_year, fiscal_quarter, ratio_code)
);

CREATE TABLE IF NOT EXISTS ops.active_company_versions (
    dataset_name text NOT NULL,
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    activated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_name, company_id)
);

CREATE TABLE IF NOT EXISTS ops.active_instrument_versions (
    dataset_name text NOT NULL,
    instrument_id bigint NOT NULL REFERENCES metadata.instruments(instrument_id),
    run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    activated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_name, instrument_id)
);

CREATE OR REPLACE VIEW gold.current_financial_values AS
SELECT g.* FROM gold.financial_values g
JOIN ops.active_company_versions a ON a.dataset_name='financials'
 AND a.company_id=g.company_id AND a.run_id=g.calculation_run_id;

CREATE OR REPLACE VIEW gold.current_financial_ratios AS
SELECT g.* FROM gold.financial_ratios g
JOIN ops.active_company_versions a ON a.dataset_name='financials'
 AND a.company_id=g.company_id AND a.run_id=g.calculation_run_id;

CREATE TABLE IF NOT EXISTS gold.price_series (
    calculation_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    instrument_id bigint NOT NULL REFERENCES metadata.instruments(instrument_id),
    price_date date NOT NULL,
    close_nominal numeric(20,6),
    close_real numeric(20,6),
    inflation_base_year integer,
    inflation_base_month smallint,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (calculation_run_id, instrument_id, price_date)
);

CREATE OR REPLACE VIEW gold.current_price_series AS
SELECT g.* FROM gold.price_series g
JOIN ops.active_instrument_versions a ON a.dataset_name='prices'
 AND a.run_id=g.calculation_run_id AND a.instrument_id=g.instrument_id;

CREATE TABLE IF NOT EXISTS ops.data_quality_results (
    quality_result_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    check_name text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('info','warning','error')),
    affected_rows bigint NOT NULL DEFAULT 0,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at timestamptz NOT NULL DEFAULT now()
);
