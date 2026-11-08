-- Financial dataset versions can receive an atomic EPS-only refresh without
-- re-parsing or recalculating financial values. Retain the replaced derived rows.
CREATE TABLE ops.eps_refresh_snapshots (
 snapshot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 refresh_run_id bigint NOT NULL REFERENCES ops.pipeline_runs(run_id),
 dataset_name text NOT NULL CHECK(dataset_name IN ('eps_values','financial_ratios','eps_coverage')),
 previous_row jsonb NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX eps_refresh_snapshots_run_idx ON ops.eps_refresh_snapshots(refresh_run_id,dataset_name);
