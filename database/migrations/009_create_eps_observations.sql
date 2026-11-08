-- EPS is a per-share measure, independent of monetary statement multipliers.
CREATE TABLE bronze.eps_sources (
    source_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    source_kind text NOT NULL CHECK (source_kind IN ('pdf','json')),
    filing_key bigint REFERENCES bronze.statement_filings(filing_key),
    source_payload jsonb NOT NULL,
    run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id,fiscal_year,fiscal_quarter,source_sha256)
);

CREATE TABLE silver.eps_observations (
    observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id bigint NOT NULL REFERENCES bronze.eps_sources(source_id),
    processing_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    period_start date NOT NULL,
    period_end date NOT NULL CHECK (period_end>=period_start),
    period_kind text NOT NULL CHECK (period_kind IN ('quarter','ytd')),
    eps_kind text NOT NULL CHECK (eps_kind IN ('basic','diluted')),
    share_class text NOT NULL,
    earnings_scope text NOT NULL CHECK (earnings_scope IN ('total','continuing')),
    currency char(3) NOT NULL CHECK (currency='TRY'),
    reported_eps numeric(28,12),
    attributable_profit numeric(28,6),
    weighted_average_shares numeric(28,8) CHECK (weighted_average_shares>0),
    share_basis_id text,
    purchasing_power_date date,
    rounding_tolerance numeric(28,12) NOT NULL CHECK (rounding_tolerance>=0),
    normalization jsonb NOT NULL,
    CHECK (reported_eps IS NOT NULL OR
           (attributable_profit IS NOT NULL AND weighted_average_shares IS NOT NULL)),
    UNIQUE (processing_run_id,source_id,period_start,period_end,period_kind,eps_kind,share_class,earnings_scope)
);

CREATE TABLE ops.active_eps_sources (
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    source_id bigint NOT NULL REFERENCES bronze.eps_sources(source_id),
    processing_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    activated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id,fiscal_year,fiscal_quarter)
);

CREATE VIEW silver.current_eps_observations AS
SELECT a.company_id,a.fiscal_year,a.fiscal_quarter,s.*
FROM silver.eps_observations s JOIN ops.active_eps_sources a
 ON a.source_id=s.source_id AND a.processing_run_id=s.processing_run_id
JOIN bronze.eps_sources source ON source.source_id=s.source_id
WHERE source.source_kind='json' OR EXISTS (
    SELECT 1 FROM ops.active_filing_versions f
    WHERE f.company_id=a.company_id AND f.statement_type='income_statement'
      AND f.fiscal_year=a.fiscal_year AND f.fiscal_quarter=a.fiscal_quarter
      AND f.filing_key=source.filing_key);

CREATE TABLE gold.eps_values (
    calculation_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
    company_id bigint NOT NULL REFERENCES metadata.companies(company_id),
    fiscal_year integer NOT NULL,
    fiscal_quarter smallint NOT NULL CHECK (fiscal_quarter BETWEEN 1 AND 4),
    eps_kind text NOT NULL CHECK (eps_kind IN ('basic','diluted')),
    share_class text NOT NULL,
    earnings_scope text NOT NULL CHECK (earnings_scope IN ('total','continuing')),
    value_quarter numeric(28,12),
    value_ytd numeric(28,12),
    value_ttm numeric(28,12),
    calculation_details jsonb NOT NULL,
    PRIMARY KEY (calculation_run_id,company_id,fiscal_year,fiscal_quarter,
                 eps_kind,share_class,earnings_scope)
);

CREATE VIEW gold.current_eps_values AS
SELECT g.* FROM gold.eps_values g JOIN ops.active_company_versions a
 ON a.dataset_name='financials' AND a.company_id=g.company_id AND a.run_id=g.calculation_run_id;
