-- Ratios expose one best-available basic-total EPS. Preserve detailed types
-- in eps_values and quality/evidence in eps_coverage.
DO $$
DECLARE snapshot_run bigint;
BEGIN
  IF EXISTS (SELECT 1 FROM gold.financial_ratios WHERE starts_with(ratio_code,'eps')) THEN
    INSERT INTO ops.pipeline_runs(pipeline_name,status,parameters)
      VALUES ('single_eps_ratio_migration','running','{"migration":"016"}')
      RETURNING run_id INTO snapshot_run;
    INSERT INTO ops.eps_refresh_snapshots(refresh_run_id,dataset_name,previous_row)
      SELECT snapshot_run,'financial_ratios',to_jsonb(r)
      FROM gold.financial_ratios r WHERE starts_with(ratio_code,'eps');

    DELETE FROM gold.financial_ratios WHERE starts_with(ratio_code,'eps') AND ratio_code<>'eps';
    UPDATE gold.financial_ratios r SET
      value_quarter=COALESCE(e.value_quarter,c.estimated_value_quarter),
      value_ytd=COALESCE(e.value_ytd,c.estimated_value_ytd),
      value_ttm=COALESCE(e.value_ttm,c.estimated_value_ttm),
      quarter_yoy_change=NULL,quarter_qoq_change=NULL,ytd_yoy_change=NULL,ttm_qoq_change=NULL,
      calculated_at=now()
    FROM gold.eps_values e LEFT JOIN gold.eps_coverage c
      ON c.calculation_run_id=e.calculation_run_id AND c.company_id=e.company_id
      AND c.fiscal_year=e.fiscal_year AND c.fiscal_quarter=e.fiscal_quarter
    WHERE r.calculation_run_id=e.calculation_run_id AND r.company_id=e.company_id
      AND r.fiscal_year=e.fiscal_year AND r.fiscal_quarter=e.fiscal_quarter
      AND r.ratio_code='eps' AND e.eps_kind='basic' AND e.share_class='ordinary' AND e.earnings_scope='total'
      AND (r.value_quarter,r.value_ytd,r.value_ttm) IS DISTINCT FROM
        (COALESCE(e.value_quarter,c.estimated_value_quarter),
         COALESCE(e.value_ytd,c.estimated_value_ytd),COALESCE(e.value_ttm,c.estimated_value_ttm));
    UPDATE ops.pipeline_runs SET status='succeeded',finished_at=now() WHERE run_id=snapshot_run;
  END IF;
END $$;

ALTER TABLE gold.financial_ratios ADD CONSTRAINT financial_ratios_single_eps_code
  CHECK (ratio_code='eps' OR NOT starts_with(ratio_code,'eps'));
