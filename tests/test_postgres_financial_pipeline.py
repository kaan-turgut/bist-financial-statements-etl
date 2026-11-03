from services.postgres_financial_pipeline import (
    _choose_active_filing_versions,
    _null_if_missing,
)


def test_null_if_missing_normalizes_nan_values_for_postgres_json():
    assert _null_if_missing(float("nan")) is None


def test_active_filing_selection_returns_one_canonical_candidate_per_period():
    candidates = [
        {
            "company_id": 10,
            "statement_type": "income_statement",
            "fiscal_year": 2017,
            "fiscal_quarter": 1,
            "filing_key": 101,
            "processing_run_id": 5,
            "report_kind": "Konsolide Olmayan",
            "filing_id": 900,
        },
        {
            "company_id": 10,
            "statement_type": "income_statement",
            "fiscal_year": 2017,
            "fiscal_quarter": 1,
            "filing_key": 102,
            "processing_run_id": 5,
            "report_kind": "Konsolide",
            "filing_id": 800,
        },
        {
            "company_id": 10,
            "statement_type": "income_statement",
            "fiscal_year": 2017,
            "fiscal_quarter": 1,
            "filing_key": 103,
            "processing_run_id": 5,
            "report_kind": "Konsolide",
            "filing_id": 850,
        },
        {
            "company_id": 10,
            "statement_type": "income_statement",
            "fiscal_year": 2017,
            "fiscal_quarter": 2,
            "filing_key": 104,
            "processing_run_id": 5,
            "report_kind": "Konsolide",
            "filing_id": 950,
        },
    ]

    selected = _choose_active_filing_versions(candidates)

    assert [(row["fiscal_quarter"], row["filing_key"]) for row in selected] == [
        (1, 103),
        (2, 104),
    ]
