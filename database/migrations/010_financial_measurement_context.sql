-- Retain reported quarter and YTD facts independently, together with report units.
DO $$ DECLARE constraint_name text;
BEGIN
  SELECT conname INTO constraint_name FROM pg_constraint
  WHERE conrelid='silver.financial_observations'::regclass AND contype='u';
  EXECUTE format('ALTER TABLE silver.financial_observations DROP CONSTRAINT %I', constraint_name);
END $$;
ALTER TABLE silver.financial_observations ADD UNIQUE NULLS NOT DISTINCT
 (processing_run_id,filing_key,source_row_id,source_column_id,line_item_path,item,period_start,period_end);
CREATE TABLE silver.financial_report_context (
 filing_key bigint NOT NULL REFERENCES bronze.statement_filings(filing_key),
 processing_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
 measurement_basis text NOT NULL CHECK (measurement_basis IN ('nominal','inflation_adjusted','unknown')),
 purchasing_power_date date,
 source_path text,
 source_sha256 text,
 evidence jsonb NOT NULL,
 PRIMARY KEY (filing_key,processing_run_id),
 CHECK (measurement_basis<>'inflation_adjusted' OR purchasing_power_date IS NOT NULL)
);
ALTER TABLE gold.financial_values
 ADD COLUMN value_real_ytd numeric(28,6),
 ADD COLUMN period_start date,
 ADD COLUMN period_end date,
 ADD COLUMN measurement_basis text,
 ADD COLUMN purchasing_power_date date,
 ADD COLUMN calculation_details jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE OR REPLACE VIEW gold.current_financial_values AS
SELECT g.* FROM gold.financial_values g JOIN ops.active_company_versions a
 ON a.dataset_name='financials' AND a.company_id=g.company_id AND a.run_id=g.calculation_run_id;
