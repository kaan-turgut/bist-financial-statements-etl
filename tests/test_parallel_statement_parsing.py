"""Exercise real spawned parsers as well as scheduling and database boundaries."""

import zipfile
from types import SimpleNamespace

import pandas as pd
import pytest

from services import postgres_financial_pipeline as pipeline


@pytest.fixture
def statement_tasks(tmp_path):
    folder = tmp_path / "TEST"
    folder.mkdir()
    archive = folder / "TEST_2023.zip"
    company = SimpleNamespace(symbol="TEST")
    tasks = []
    with zipfile.ZipFile(archive, "w") as zipped:
        for quarter, date in [(3, "30.09.2023"), (1, "31.03.2023"), (2, "30.06.2023")]:
            member = f"TEST_{100 + quarter}_2023_{quarter}.xls"
            html = f"""
            <div>Yıl:2023 Periyot:{quarter}
              <table>
                <tr><td>Finansal Tablo Niteliği</td><td>Konsolide</td></tr>
                <tr><td>Sunum Para Birimi</td><td>1.000 TL</td></tr>
              </table>
            </div>
            <table class="financial-table">
              <tr><td class="context-header"><div class="gwt-Label">{date}</div></td></tr>
              <tr><td><div class="gwt-Label">Bilanço</div></td></tr>
              <tr class="general_role_210000-row-4 data-input-row">
                <td><div class="gwt-Label">Nakit ve Nakit Benzerleri</div></td>
                <td class="taxonomy-context-value col-order-class-4"><div class="gwt-Label">{quarter}.000</div></td>
              </tr>
            </table>
            """
            zipped.writestr(member, html)
            tasks.append((company, f"{archive}/{member}"))
    return tasks


def test_spawned_parsing_matches_serial_and_preserves_source_order(statement_tasks):
    serial = list(pipeline._parse_statement_frames(statement_tasks, 1))
    parallel = list(pipeline._parse_statement_frames(statement_tasks, 2))
    assert [path for _, path, _ in parallel] == [path for _, path, _ in serial]
    for (_, _, expected), (_, _, actual) in zip(serial, parallel):
        pd.testing.assert_frame_equal(actual, expected)
        assert len(actual) == 1
        assert actual["reporting unit"].iloc[0] == 1000
        assert actual["report_kind"].iloc[0] == "Konsolide"


def test_parallel_parse_failure_happens_before_database_access(statement_tasks, tmp_path, monkeypatch):
    archive = tmp_path / "TEST" / "TEST_2023.zip"
    with zipfile.ZipFile(archive, "a") as zipped:
        zipped.writestr("TEST_104_2023_4.xls", "broken report")
    monkeypatch.setattr(pipeline, "FINANCIAL_STATEMENTS_DIR", tmp_path)

    def unexpected_connection():
        pytest.fail("Parsing failures must not open a database connection")

    monkeypatch.setattr(pipeline, "connect_metadata_db", unexpected_connection)
    with pytest.raises(RuntimeError, match="TEST_104_2023_4.xls"):
        pipeline.ingest_statements_postgres([statement_tasks[0][0]], 2023, 1, 2023, 4)


def test_parallel_ingestion_matches_serial_database_results(database, statement_tasks, tmp_path, monkeypatch):
    conn, _, _ = database
    monkeypatch.setattr(pipeline, "FINANCIAL_STATEMENTS_DIR", tmp_path)
    company = statement_tasks[0][0]

    def observations():
        return conn.execute("""SELECT fiscal_quarter, period_start, period_end, amount
            FROM silver.financial_observations
            WHERE (filing_key, processing_run_id) IN
              (SELECT filing_key, processing_run_id FROM ops.active_filing_versions)
            ORDER BY fiscal_quarter""").fetchall()

    assert pipeline.ingest_statements_postgres([company], 2023, 1, 2023, 3, parse_workers=1) == 3
    expected = observations()
    assert len(expected) == 3
    conn.commit()
    assert pipeline.ingest_statements_postgres([company], 2023, 1, 2023, 3, parse_workers=2) == 3
    assert observations() == expected
    assert conn.execute("SELECT COUNT(*) AS n FROM bronze.statement_filings").fetchone()["n"] == 3


def test_scheduler_bounds_inflight_tasks_and_cancels_on_close(monkeypatch):
    class Future:
        def result(self):
            executor.outstanding -= 1
            return pd.DataFrame()

        def cancel(self):
            executor.cancelled += 1

    class Executor:
        def __init__(self, **kwargs):
            self.outstanding = self.peak = self.cancelled = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def submit(self, *args):
            self.outstanding += 1
            self.peak = max(self.peak, self.outstanding)
            return Future()

    executor = Executor()
    monkeypatch.setattr(pipeline, "ProcessPoolExecutor", lambda **kwargs: executor)
    tasks = [(SimpleNamespace(symbol="TEST"), str(i)) for i in range(20)]
    results = pipeline._parse_statement_frames(tasks, 2)
    next(results)
    assert executor.peak == 4
    results.close()
    assert executor.cancelled == 4
    executor = Executor()
    assert [path for _, path, _ in pipeline._parse_statement_frames(tasks, 2)] == [str(i) for i in range(20)]
    assert executor.peak == 4


@pytest.mark.parametrize("workers", [0, -1, 1.5, True])
def test_programmatic_worker_validation(workers):
    with pytest.raises(ValueError, match="positive integer"):
        pipeline.update_database_postgres([], 2023, 1, 2023, 4, parse_workers=workers)
