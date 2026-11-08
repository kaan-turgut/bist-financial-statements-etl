"""EPS source ingestion, period calculations and publication without share proxies."""

import hashlib
import json
import logging
import unicodedata
import zipfile
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from dataclasses import fields, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from multiprocessing import get_context
import sys
from unidecode import unidecode
from types import SimpleNamespace

from models.earnings_per_share import (
    EpsObservation,
    calculate_history,
    normalize_observation,
)

logger = logging.getLogger(__name__)
RATIO_CODES = {
    ("basic", "total"): "eps",
    ("diluted", "total"): "eps_diluted",
    ("basic", "continuing"): "eps_continuing",
    ("diluted", "continuing"): "eps_continuing_diluted",
}


def extract_filing_eps(
    zipped, statement_member, fiscal_year, fiscal_quarter, period_end, *, ocr=False
):
    from read_statements.eps import extract_pdf_eps, pdf_pages
    from read_statements.eps_recovery import extract_recoverable_eps
    from read_statements.structured_eps import (
        structured_eps_cells,
        normalize_structured_cells,
        merge_eps_observations,
        reconcile_structured_pdf,
    )

    statement = zipped.read(statement_member)
    cells, warnings = structured_eps_cells(statement, fiscal_quarter, period_end)
    members = [
        m
        for m in zipped.namelist()
        if m.lower().endswith(".pdf")
        and f"Dönem{fiscal_quarter}Ekler/" in unicodedata.normalize("NFC", m)
    ]
    turkish = [
        m
        for m in members
        if not any(
            t in m.lower() for t in ("eng", "i̇ng", "ingiliz", "english", "cmb report")
        )
    ]
    references, pdf_sources = [], []
    digest = hashlib.sha256(
        b"structured_eps_v7\0" + (b"ocr\0" if ocr else b"") + statement
    )
    for member in turkish or members:
        data = zipped.read(member)
        source_hash = hashlib.sha256(data).hexdigest()
        digest.update(source_hash.encode())
        try:
            pages = pdf_pages(data, ocr=True) if ocr else pdf_pages(data)
            observations, payload = extract_pdf_eps(
                data, fiscal_year, fiscal_quarter, pages=pages
            )
            recovered, recovery_warnings = extract_recoverable_eps(
                pages, fiscal_quarter, period_end
            )
            observations = merge_eps_observations([*observations, *recovered])
            warnings.extend(recovery_warnings)
            reconciled, unit_warnings = reconcile_structured_pdf(pages, cells)
            observations = merge_eps_observations([*observations, *reconciled])
            warnings.extend(unit_warnings)
        except ValueError as exc:
            # Genuine reconciliation failures must not be bypassed through a
            # second representation of the same source report.
            if any(
                word in str(exc)
                for word in ("reconciliation", "disagree", "does not reconcile")
            ):
                raise
            warnings.append(f"{member}: {exc}")
            continue
        matching = [o for o in observations if o.period_end == period_end]
        references.extend(matching)
        warnings.extend(f"{member}: {w}" for w in payload["extraction_warnings"][:3])
        pdf_sources.append(
            {
                "member": member,
                "sha256": source_hash,
                "payload": payload,
                "accepted_observations": len(matching),
            }
        )
    references = merge_eps_observations(references)
    structured, unit_warnings = normalize_structured_cells(cells, references)
    merged = merge_eps_observations([*references, *structured])
    if not merged:
        raise ValueError(
            "No EPS with verified units and matching reporting dates; "
            + "; ".join([*warnings, *unit_warnings][:3])
        )
    return (
        merged,
        {
            "structured_cells": cells,
            "pdf_sources": pdf_sources,
            "extraction_warnings": [*warnings, *unit_warnings],
        },
        digest.hexdigest(),
    )


def preserve_verified_eps(conn, filing, observations, payload):
    """Retain complementary verified facts; quarantine disagreements."""
    from read_statements.structured_eps import merge_eps_observations

    columns = {field.name for field in fields(EpsObservation)}
    previous = conn.execute(
        """SELECT s.* FROM silver.current_eps_observations s
        JOIN bronze.eps_sources f USING(source_id)
        WHERE s.company_id=%s AND s.fiscal_year=%s AND s.fiscal_quarter=%s AND f.filing_key=%s""",
        (
            filing["company_id"],
            filing["fiscal_year"],
            filing["fiscal_quarter"],
            filing["filing_key"],
        ),
    ).fetchall()
    payload["preserved_source_ids"] = sorted({s["source_id"] for s in previous})
    old = [
        EpsObservation(**{k: v for k, v in row.items() if k in columns})
        for row in previous
    ]
    # Earlier note parsing defaulted every row to basic/total. Correct that
    # specific classification only when an explicit label, dates, page and
    # reconciled value identify the same source fact under its proper identity.
    retained = []
    for previous in old:
        corrected = False
        if previous.normalization.get("validation") == "explicit_nominal_pdf_layout":
            from read_statements.eps import _eps_unit
            from read_statements.eps_recovery import per_nominal_unit

            for current in observations:
                evidence = current.normalization.get("source_unit_evidence", "")
                declared = per_nominal_unit(evidence)
                declared_unit = _eps_unit(
                    current.normalization.get("source_eps_label", "")
                ) or (declared[0] if declared else None)
                if (
                    current.normalization.get("validation")
                    == "explicit_nominal_pdf_layout"
                    and current.normalization.get("eps_unit") == declared_unit
                    and (
                        previous.normalization.get("eps_unit") != declared_unit
                        or (
                            declared is not None
                            and Decimal(
                                previous.normalization.get("share_nominal_value", "1")
                            )
                            != declared[1]
                            and Decimal(
                                current.normalization.get("share_nominal_value", "1")
                            )
                            == declared[1]
                        )
                    )
                    and current.normalization.get("source_page")
                    == previous.normalization.get("source_page")
                    and current.normalization.get("source_column_index")
                    == previous.normalization.get("source_column_index")
                    and current.normalization.get("raw_reported_eps")
                    == previous.normalization.get("raw_reported_eps")
                    and (
                        current.period_start,
                        current.period_end,
                        current.period_kind,
                        current.eps_kind,
                        current.earnings_scope,
                        current.share_class,
                    )
                    == (
                        previous.period_start,
                        previous.period_end,
                        previous.period_kind,
                        previous.eps_kind,
                        previous.earnings_scope,
                        previous.share_class,
                    )
                ):
                    payload.setdefault("corrected_legacy_units", []).append(
                        {
                            "source_id": previous.source_id,
                            "from": previous.normalization.get("eps_unit"),
                            "to": declared_unit,
                        }
                    )
                    corrected = True
                    break
        if (
            not corrected
            and previous.normalization.get("validation") == "reconciled_note"
        ):
            for current in observations:
                label = unidecode(
                    current.normalization.get("source_eps_label", "")
                ).lower()
                same = (
                    previous.period_start == current.period_start
                    and previous.period_end == current.period_end
                    and previous.share_class == current.share_class
                    and previous.normalization.get("source_page")
                    == current.normalization.get("source_page")
                    and abs(previous.value - current.value)
                    <= previous.rounding_tolerance + current.rounding_tolerance
                )
                scope_fix = (
                    previous.earnings_scope == "total"
                    and current.earnings_scope == "continuing"
                    and "surdurulen" in label
                )
                kind_fix = (
                    previous.eps_kind == "basic"
                    and current.eps_kind == "diluted"
                    and any(word in label for word in ("sulandirilmis", "seyreltilmis"))
                    and not any(word in label for word in ("adi/", "temel", "basit"))
                )
                if same and (scope_fix or kind_fix):
                    payload.setdefault("corrected_legacy_classifications", []).append(
                        {
                            "source_id": previous.source_id,
                            "period_kind": previous.period_kind,
                            "from": [previous.eps_kind, previous.earnings_scope],
                            "to": [current.eps_kind, current.earnings_scope],
                        }
                    )
                    corrected = True
                    break
        elif previous.normalization.get("validation", "").startswith(
            "structured_dates_"
        ):
            for current in observations:
                parent = unidecode(
                    current.normalization.get("parent_label") or ""
                ).lower()
                same = (
                    previous.period_start == current.period_start
                    and previous.period_end == current.period_end
                    and previous.share_class == current.share_class
                    and previous.normalization.get("source_row_id")
                    == current.normalization.get("source_row_id")
                    and previous.normalization.get("source_column_index")
                    == current.normalization.get("source_column_index")
                    and abs(previous.value - current.value)
                    <= previous.rounding_tolerance + current.rounding_tolerance
                )
                scope_fix = (
                    previous.earnings_scope == "total"
                    and current.earnings_scope == "continuing"
                    and "surdurulen" in parent
                )
                kind_fix = (
                    previous.eps_kind == "basic"
                    and current.eps_kind == "diluted"
                    and any(
                        word in parent for word in ("sulandirilmis", "seyreltilmis")
                    )
                )
                if same and (scope_fix or kind_fix):
                    payload.setdefault("corrected_legacy_classifications", []).append(
                        {
                            "source_id": previous.source_id,
                            "period_kind": previous.period_kind,
                            "from": [previous.eps_kind, previous.earnings_scope],
                            "to": [current.eps_kind, current.earnings_scope],
                        }
                    )
                    corrected = True
                    break
        if not corrected:
            retained.append(previous)
    return merge_eps_observations([*observations, *retained])


def _extract_local_filing(filing):
    from services.postgres_financial_pipeline import _split_archive_path

    try:
        archive, member = _split_archive_path(filing["source_path"])
        with zipfile.ZipFile(archive) as zipped:
            return extract_filing_eps(
                zipped,
                member,
                filing["fiscal_year"],
                filing["fiscal_quarter"],
                filing["period_end"],
                ocr=filing.get("ocr", False),
            )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return exc
    except ArithmeticError as exc:
        return ValueError(f"Invalid numeric EPS source: {exc}")


def quarantine_conflicting_eps_source(conn, filing, run_id, error):
    """Deactivate only contradicted automatic sources; preserve their history."""
    if not any(
        word in str(error)
        for word in ("disagree", "reconciliation", "does not reconcile")
    ):
        return False
    previous = conn.execute(
        """SELECT a.* FROM ops.active_eps_sources a
        JOIN bronze.eps_sources f USING(source_id)
        WHERE a.company_id=%s AND a.fiscal_year=%s AND a.fiscal_quarter=%s
          AND f.filing_key=%s AND f.source_kind<>'json'""",
        (
            filing["company_id"],
            filing["fiscal_year"],
            filing["fiscal_quarter"],
            filing["filing_key"],
        ),
    ).fetchone()
    if previous is None:
        return False
    conn.execute(
        "DELETE FROM ops.active_eps_sources WHERE company_id=%s AND fiscal_year=%s AND fiscal_quarter=%s",
        (filing["company_id"], filing["fiscal_year"], filing["fiscal_quarter"]),
    )
    conn.execute(
        """INSERT INTO ops.data_quality_results(run_id,check_name,severity,affected_rows,details)
        VALUES (%s,'eps_conflicting_source_quarantined','error',1,%s::jsonb)""",
        (
            run_id,
            json.dumps(
                {"previous_active_source": previous, "reason": str(error)}, default=str
            ),
        ),
    )
    return True


def _prepared_eps_filings(filings, workers):
    """Bound PDF extraction to two pending reports per worker; no worker SQL."""
    if workers == 1:
        for filing in filings:
            yield filing, _extract_local_filing(filing)
        return
    # CLI processes bypass the GIL and do not inherit SQL connections. Calls
    # from stdin/notebooks use threads because spawn cannot import their main.
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    executor_type = (
        ProcessPoolExecutor
        if main_file and Path(main_file).is_file()
        else ThreadPoolExecutor
    )
    options = {"max_workers": workers}
    if executor_type is ProcessPoolExecutor:
        options["mp_context"] = get_context("spawn")
    with executor_type(**options) as executor:
        remaining = iter(filings)
        pending = deque()

        def submit_next():
            filing = next(remaining, None)
            if filing is not None:
                pending.append((filing, executor.submit(_extract_local_filing, filing)))

        try:
            for _ in range(workers * 2):
                submit_next()
            while pending:
                filing, future = pending.popleft()
                result = future.result()
                submit_next()
                yield filing, result
        finally:
            for _, future in pending:
                future.cancel()


def calculate_eps_ratios(conn, run_id, company_ids=None):
    condition = " AND company_id=ANY(%s)" if company_ids is not None else ""
    params = (run_id, company_ids) if company_ids is not None else (run_id,)
    periods = conn.execute(
        """SELECT DISTINCT company_id,fiscal_year,fiscal_quarter
        FROM gold.financial_values WHERE calculation_run_id=%s"""
        + condition,
        params,
    ).fetchall()
    company_ids = sorted({row["company_id"] for row in periods})
    if not company_ids:
        return
    # An EPS-only refresh must also retire share classes no longer supported by
    # current sources. Its caller snapshots the previous derived rows first.
    conn.execute(
        "DELETE FROM gold.eps_values WHERE calculation_run_id=%s AND company_id=ANY(%s)",
        (run_id, company_ids),
    )
    from services.eps_coverage import load_capital_histories, calculate_eps_coverage
    from models.eps_capital import verified_share_basis
    from models.financial_values import CpiSeries

    capital = load_capital_histories(conn, company_ids)
    cpi = CpiSeries(
        conn.execute(
            "SELECT observation_date,value FROM silver.monthly_macro_data WHERE series_code='CPI_MONTHLY_RATE'"
        ).fetchall()
    )
    history = defaultdict(dict)
    for row in periods:
        history[row["company_id"]][(row["fiscal_year"], row["fiscal_quarter"])] = []
    columns = {field.name for field in fields(EpsObservation)}
    sources = conn.execute(
        """SELECT s.*,b.source_kind,c.measurement_basis AS report_measurement_basis,
            c.purchasing_power_date AS report_purchasing_power_date
        FROM silver.current_eps_observations s JOIN bronze.eps_sources b USING(source_id)
        LEFT JOIN ops.active_filing_versions a ON a.filing_key=b.filing_key
          AND a.statement_type='income_statement'
        LEFT JOIN silver.financial_report_context c
          ON c.filing_key=a.filing_key AND c.processing_run_id=a.processing_run_id
        WHERE s.company_id=ANY(%s)""",
        (company_ids,),
    ).fetchall()
    for row in sources:
        key = (row["fiscal_year"], row["fiscal_quarter"])
        obs = EpsObservation(**{k: v for k, v in row.items() if k in columns})
        if (
            obs.purchasing_power_date is None
            and row["report_measurement_basis"] == "inflation_adjusted"
            and row["report_purchasing_power_date"] is not None
        ):
            obs = replace(
                obs,
                purchasing_power_date=row["report_purchasing_power_date"],
                normalization={
                    **obs.normalization,
                    "measurement_basis_evidence": "current_filing_report_context",
                },
            )
        basis_known = (
            row["source_kind"] == "json"
            or obs.purchasing_power_date is not None
            or row["report_measurement_basis"] == "nominal"
        )
        if basis_known:
            obs = verified_share_basis(obs, capital.get(row["company_id"]))
        history[row["company_id"]].setdefault(key, []).append(obs)
    for company_id, company_history in history.items():
        calculated = calculate_history(company_history, cpi)
        for row in periods:
            if row["company_id"] != company_id:
                continue
            year, quarter = row["fiscal_year"], row["fiscal_quarter"]
            groups = {(kind, "ordinary", scope) for kind, scope in RATIO_CODES}
            groups |= {key[2:] for key in calculated if key[:2] == (year, quarter)}
            for kind, share_class, scope in sorted(groups):
                result = calculated.get(
                    (year, quarter, kind, share_class, scope),
                    {
                        "value_quarter": None,
                        "value_ytd": None,
                        "value_ttm": None,
                        "details": {
                            "unit": "TRY_per_1_TRY_nominal",
                            "unavailable_reason": "no_verified_eps_source",
                        },
                    },
                )
                values = (
                    result["value_quarter"],
                    result["value_ytd"],
                    result["value_ttm"],
                )
                conn.execute(
                    """INSERT INTO gold.eps_values
                    (calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,
                     earnings_scope,value_quarter,value_ytd,value_ttm,calculation_details)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (calculation_run_id,company_id,fiscal_year,fiscal_quarter,eps_kind,share_class,earnings_scope)
                    DO UPDATE SET value_quarter=EXCLUDED.value_quarter,value_ytd=EXCLUDED.value_ytd,
                                  value_ttm=EXCLUDED.value_ttm,calculation_details=EXCLUDED.calculation_details""",
                    (
                        run_id,
                        company_id,
                        year,
                        quarter,
                        kind,
                        share_class,
                        scope,
                        *values,
                        json.dumps(result["details"], default=str),
                    ),
                )
                if share_class == "ordinary":
                    conn.execute(
                        """INSERT INTO gold.financial_ratios
                        (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code,value_quarter,value_ytd,value_ttm)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (calculation_run_id,company_id,fiscal_year,fiscal_quarter,ratio_code)
                        DO UPDATE SET value_quarter=EXCLUDED.value_quarter,value_ytd=EXCLUDED.value_ytd,
                                      value_ttm=EXCLUDED.value_ttm,calculated_at=now()""",
                        (
                            run_id,
                            company_id,
                            year,
                            quarter,
                            RATIO_CODES[(kind, scope)],
                            *values,
                        ),
                    )
    logger.info(
        "EPS coverage assessment: %s",
        calculate_eps_coverage(conn, run_id, capital, cpi, company_ids),
    )


def store_eps_source(
    conn,
    *,
    company_id,
    year,
    quarter,
    source_path,
    source_hash,
    source_kind,
    payload,
    observations,
    run_id,
    filing_key=None,
):
    if not observations:
        raise ValueError("Cannot activate an EPS source without verified observations.")
    if any(
        o.period_kind == "ytd" and not quarter * 75 <= o.days <= quarter * 100
        for o in observations
    ):
        raise ValueError("EPS YTD period does not match the supplied fiscal quarter.")
    identities = [
        (o.period_kind, o.eps_kind, o.share_class, o.earnings_scope)
        for o in observations
    ]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate EPS observations in source.")
    if len({o.period_end for o in observations}) != 1:
        raise ValueError("EPS source observations must share one reporting-period end.")
    source = conn.execute(
        """INSERT INTO bronze.eps_sources
        (company_id,fiscal_year,fiscal_quarter,source_path,source_sha256,source_kind,filing_key,source_payload,run_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
        ON CONFLICT (company_id,fiscal_year,fiscal_quarter,source_sha256) DO NOTHING RETURNING source_id""",
        (
            company_id,
            year,
            quarter,
            source_path,
            source_hash,
            source_kind,
            filing_key,
            json.dumps(payload, default=str, ensure_ascii=False),
            run_id,
        ),
    ).fetchone()
    if source is None:
        source = conn.execute(
            """SELECT source_id FROM bronze.eps_sources
            WHERE company_id=%s AND fiscal_year=%s AND fiscal_quarter=%s AND source_sha256=%s""",
            (company_id, year, quarter, source_hash),
        ).fetchone()
    source_id = source["source_id"]
    for o in observations:
        conn.execute(
            """INSERT INTO silver.eps_observations
            (source_id,processing_run_id,period_start,period_end,period_kind,eps_kind,share_class,earnings_scope,currency,
             reported_eps,attributable_profit,weighted_average_shares,share_basis_id,purchasing_power_date,
             rounding_tolerance,normalization)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                source_id,
                run_id,
                o.period_start,
                o.period_end,
                o.period_kind,
                o.eps_kind,
                o.share_class,
                o.earnings_scope,
                o.currency,
                o.reported_eps,
                o.attributable_profit,
                o.weighted_average_shares,
                o.share_basis_id,
                o.purchasing_power_date,
                o.rounding_tolerance,
                json.dumps(o.normalization, default=str),
            ),
        )
    conn.execute(
        """INSERT INTO ops.active_eps_sources
        (company_id,fiscal_year,fiscal_quarter,source_id,processing_run_id) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (company_id,fiscal_year,fiscal_quarter)
        DO UPDATE SET source_id=EXCLUDED.source_id,processing_run_id=EXCLUDED.processing_run_id,activated_at=now()""",
        (company_id, year, quarter, source_id, run_id),
    )


def calculate_eps_changes(conn, run_id, company_ids=None):
    """Suppress EPS growth unless both observations have verified common bases."""
    # The new run is still uncommitted, so autovacuum cannot gather its statistics.
    # Without these, PostgreSQL can plan each join as if it had only one EPS row.
    conn.execute("ANALYZE gold.eps_values")
    conn.execute(
        """WITH compared AS (
        SELECT e.*,y.value_quarter AS previous_year_quarter,y.value_ytd AS previous_ytd,
               p.value_quarter AS previous_quarter,p.value_ttm AS previous_ttm,
               y.calculation_details AS annual_details,p.calculation_details AS preceding_details
        FROM gold.eps_values e
        LEFT JOIN gold.eps_values y ON y.calculation_run_id=e.calculation_run_id
          AND y.company_id=e.company_id AND y.eps_kind=e.eps_kind AND y.share_class=e.share_class
          AND y.earnings_scope=e.earnings_scope AND y.fiscal_year=e.fiscal_year-1 AND y.fiscal_quarter=e.fiscal_quarter
        LEFT JOIN gold.eps_values p ON p.calculation_run_id=e.calculation_run_id
          AND p.company_id=e.company_id AND p.eps_kind=e.eps_kind AND p.share_class=e.share_class
          AND p.earnings_scope=e.earnings_scope AND p.fiscal_year*4+p.fiscal_quarter=e.fiscal_year*4+e.fiscal_quarter-1
        WHERE e.calculation_run_id=%s AND e.share_class='ordinary'
    ) UPDATE gold.financial_ratios r SET
        quarter_yoy_change=CASE WHEN e.calculation_details->'comparison_basis'->>'quarter' IS NOT NULL
          AND e.calculation_details->'comparison_basis'->>'quarter'=e.annual_details->'comparison_basis'->>'quarter'
          THEN e.value_quarter/NULLIF(e.previous_year_quarter,0)-1 END,
        quarter_qoq_change=CASE WHEN e.calculation_details->'comparison_basis'->>'quarter' IS NOT NULL
          AND e.calculation_details->'comparison_basis'->>'quarter'=e.preceding_details->'comparison_basis'->>'quarter'
          THEN e.value_quarter/NULLIF(e.previous_quarter,0)-1 END,
        ytd_yoy_change=CASE WHEN e.calculation_details->'comparison_basis'->>'ytd' IS NOT NULL
          AND e.calculation_details->'comparison_basis'->>'ytd'=e.annual_details->'comparison_basis'->>'ytd'
          THEN e.value_ytd/NULLIF(e.previous_ytd,0)-1 END,
        ttm_qoq_change=CASE WHEN e.calculation_details->'comparison_basis'->>'ttm' IS NOT NULL
          AND e.calculation_details->'comparison_basis'->>'ttm'=e.preceding_details->'comparison_basis'->>'ttm'
          THEN e.value_ttm/NULLIF(e.previous_ttm,0)-1 END
        FROM compared e WHERE r.calculation_run_id=e.calculation_run_id AND r.company_id=e.company_id
          AND r.fiscal_year=e.fiscal_year AND r.fiscal_quarter=e.fiscal_quarter
          AND r.ratio_code=CASE
            WHEN e.eps_kind='basic' AND e.earnings_scope='total' THEN 'eps'
            WHEN e.eps_kind='diluted' AND e.earnings_scope='total' THEN 'eps_diluted'
            WHEN e.eps_kind='basic' THEN 'eps_continuing' ELSE 'eps_continuing_diluted' END"""
        + (" AND r.company_id=ANY(%s)" if company_ids is not None else ""),
        (run_id, company_ids) if company_ids is not None else (run_id,),
    )


def _refresh_published(conn, company_ids):
    return conn.execute(
        """SELECT DISTINCT c.symbol,g.inflation_base_year,g.inflation_base_month
        FROM gold.current_financial_values g JOIN metadata.companies c USING (company_id)
        WHERE g.company_id=ANY(%s)""",
        (company_ids,),
    ).fetchall()


def _rebuild(published):
    if not published:
        return []
    return [
        refresh_eps_postgres(
            [
                SimpleNamespace(symbol=s)
                for s in sorted({r["symbol"] for r in published})
            ]
        )
    ]


def refresh_eps_postgres(companies=None):
    """Atomically refresh only derived EPS/coverage/P/E; retain prior snapshots.

    calculation_run_id remains the financial dataset version. The actual EPS
    refresh run is recorded in calculation_details and ops snapshot history.
    """
    from psycopg.types.json import Jsonb
    from services import postgres_financial_pipeline as financials
    from services.price_earnings import calculate_price_earnings

    financials.apply_medallion_migration()
    with financials.connect_metadata_db() as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext('bist_finance_price_earnings'))")
        condition = " AND c.symbol=ANY(%s)" if companies is not None else ""
        params = ([c.symbol for c in companies],) if companies is not None else ()
        rows = conn.execute(
            """SELECT a.run_id,c.company_id,c.symbol
            FROM ops.active_company_versions a JOIN metadata.companies c USING(company_id)
            WHERE a.dataset_name='financials'"""
            + condition,
            params,
        ).fetchall()
        refresh_run = financials._run_start(
            conn, "eps_gold_refresh", {"symbols": [r["symbol"] for r in rows]}
        )
        conn.commit()
        try:
            groups = defaultdict(list)
            for row in rows:
                groups[row["run_id"]].append(row)
            for financial_run, selected in groups.items():
                ids = [r["company_id"] for r in selected]
                for table in ("eps_values", "financial_ratios", "eps_coverage"):
                    ratio_filter = (
                        " AND (starts_with(g.ratio_code,'eps') OR g.ratio_code='price_earnings')"
                        if table == "financial_ratios"
                        else ""
                    )
                    conn.execute(
                        f"""INSERT INTO ops.eps_refresh_snapshots(refresh_run_id,dataset_name,previous_row)
                        SELECT %s,%s,to_jsonb(g) FROM gold.{table} g
                        WHERE g.calculation_run_id=%s AND g.company_id=ANY(%s)"""
                        + ratio_filter,
                        (refresh_run, table, financial_run, ids),
                    )
                calculate_eps_ratios(conn, financial_run, ids)
                conn.execute(
                    """UPDATE gold.eps_values SET calculation_details=calculation_details||%s
                    WHERE calculation_run_id=%s AND company_id=ANY(%s)""",
                    (Jsonb({"eps_refresh_run_id": refresh_run}), financial_run, ids),
                )
                calculate_eps_changes(conn, financial_run, ids)
                calculate_price_earnings(
                    conn,
                    refresh_run,
                    financial_run_id=financial_run,
                    companies=[SimpleNamespace(symbol=r["symbol"]) for r in selected],
                )
            financials._run_finish(conn, refresh_run)
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            financials._run_finish(conn, refresh_run, exc)
            conn.commit()
            raise
        return refresh_run


def update_eps_postgres(
    companies=None, *, rebuild_gold=True, extraction_workers=2, ocr=False
):
    from services import postgres_financial_pipeline as financials

    if type(extraction_workers) is not int or extraction_workers < 1:
        raise ValueError("extraction_workers must be a positive integer.")
    financials.apply_medallion_migration()
    with financials.connect_metadata_db() as conn:
        filter_sql = " AND c.symbol=ANY(%s)" if companies is not None else ""
        params = ([c.symbol for c in companies],) if companies is not None else ()
        if companies is None:
            filter_sql = " AND EXISTS (SELECT 1 FROM ops.active_company_versions g WHERE g.company_id=c.company_id AND g.dataset_name='financials')"
        filings = conn.execute(
            """SELECT DISTINCT c.company_id,c.symbol,f.filing_key,f.fiscal_year,f.fiscal_quarter,
                f.source_path,MAX(s.period_end) AS period_end
            FROM ops.active_filing_versions a JOIN bronze.statement_filings f USING (filing_key)
            JOIN metadata.companies c ON c.company_id=a.company_id
            JOIN silver.current_financial_observations s ON s.filing_key=f.filing_key
            WHERE a.statement_type='income_statement'"""
            + filter_sql
            + """
            GROUP BY c.company_id,c.symbol,f.filing_key,f.fiscal_year,f.fiscal_quarter,f.source_path
            ORDER BY c.symbol,f.fiscal_year,f.fiscal_quarter""",
            params,
        ).fetchall()
        company_ids = sorted({f["company_id"] for f in filings})
        published = _refresh_published(conn, company_ids) if rebuild_gold else []
        run_id = financials._run_start(
            conn,
            "eps_source_ingestion",
            {
                "symbols": [c.symbol for c in companies]
                if companies is not None
                else None,
                "extraction_workers": extraction_workers,
                "method": "structured_dates_with_pdf_unit_verification",
            },
        )
        conn.commit()
        verified_periods = {
            (r["company_id"], r["fiscal_year"], r["fiscal_quarter"])
            for r in conn.execute("""
            SELECT a.company_id,a.fiscal_year,a.fiscal_quarter FROM ops.active_eps_sources a
            JOIN bronze.eps_sources f USING(source_id) WHERE f.source_kind='json'""")
        }
        work = [
            f
            for f in filings
            if (f["company_id"], f["fiscal_year"], f["fiscal_quarter"])
            not in verified_periods
        ]
        for filing in work:
            filing["ocr"] = ocr
        stored, preserved, unavailable = 0, len(filings) - len(work), []
        logger.info(
            "Checking %s local EPS filings with %s extraction workers; preserving %s verified JSON sources",
            len(work),
            extraction_workers,
            preserved,
        )
        try:
            for index, (filing, prepared) in enumerate(
                _prepared_eps_filings(work, extraction_workers), 1
            ):
                try:
                    if isinstance(prepared, Exception):
                        raise prepared
                    observations, payload, source_hash = prepared
                    observations = preserve_verified_eps(
                        conn, filing, observations, payload
                    )
                    store_eps_source(
                        conn,
                        company_id=filing["company_id"],
                        year=filing["fiscal_year"],
                        quarter=filing["fiscal_quarter"],
                        source_path=filing["source_path"],
                        source_hash=source_hash,
                        source_kind="kap_html",
                        filing_key=filing["filing_key"],
                        payload=payload,
                        observations=observations,
                        run_id=run_id,
                    )
                    stored += 1
                except (OSError, ValueError, zipfile.BadZipFile) as exc:
                    quarantine_conflicting_eps_source(conn, filing, run_id, exc)
                    unavailable.append(
                        {
                            "symbol": filing["symbol"],
                            "year": filing["fiscal_year"],
                            "quarter": filing["fiscal_quarter"],
                            "reason": str(exc),
                        }
                    )
                if index % 25 == 0 or index == len(work):
                    logger.info(
                        "EPS sources checked %s/%s; accepted %s",
                        index,
                        len(work),
                        stored,
                    )
            conn.execute(
                """INSERT INTO ops.data_quality_results (run_id,check_name,severity,affected_rows,details)
                VALUES (%s,'eps_source_unavailable','warning',%s,%s::jsonb)""",
                (run_id, len(unavailable), json.dumps(unavailable)),
            )
            financials._run_finish(conn, run_id)
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            financials._run_finish(conn, run_id, exc)
            conn.commit()
            raise
    gold_runs = _rebuild(published)
    return {
        "run_id": run_id,
        "accepted": stored,
        "preserved_verified": preserved,
        "unavailable": len(unavailable),
        "gold_runs": gold_runs,
    }


def import_eps_postgres(path):
    """Import source-verified JSON inputs, including explicit split/basis factors."""
    from services import postgres_financial_pipeline as financials

    path = Path(path)
    raw = path.read_bytes()
    records = json.loads(raw, parse_float=Decimal)["observations"]
    prepared = defaultdict(list)
    for record in records:
        values = dict(record)
        symbol, year, quarter = (
            values.pop("symbol").upper(),
            int(values.pop("fiscal_year")),
            int(values.pop("fiscal_quarter")),
        )
        if not 1 <= quarter <= 4:
            raise ValueError("EPS fiscal quarter must be between 1 and 4.")
        for key in ("period_start", "period_end", "purchasing_power_date"):
            if values.get(key) is not None:
                values[key] = date.fromisoformat(values[key])
        prepared[(symbol, year, quarter)].append(normalize_observation(**values))
    if not prepared:
        raise ValueError("EPS import contains no observations.")
    financials.apply_medallion_migration()
    with financials.connect_metadata_db() as conn:
        companies = {
            r["symbol"]: r["company_id"]
            for r in conn.execute(
                "SELECT company_id,symbol FROM metadata.companies WHERE symbol=ANY(%s)",
                (sorted({key[0] for key in prepared}),),
            ).fetchall()
        }
        if {k[0] for k in prepared} - companies.keys():
            raise ValueError("Unknown EPS company symbol.")
        published = _refresh_published(conn, list(companies.values()))
        run_id = financials._run_start(
            conn, "eps_json_ingestion", {"source_path": str(path)}
        )
        conn.commit()
        try:
            for (symbol, year, quarter), observations in prepared.items():
                store_eps_source(
                    conn,
                    company_id=companies[symbol],
                    year=year,
                    quarter=quarter,
                    source_path=str(path.resolve()),
                    source_hash=hashlib.sha256(raw).hexdigest(),
                    source_kind="json",
                    payload={"observations": records},
                    observations=observations,
                    run_id=run_id,
                )
            financials._run_finish(conn, run_id)
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            financials._run_finish(conn, run_id, exc)
            conn.commit()
            raise
    return {
        "run_id": run_id,
        "periods": len(prepared),
        "gold_runs": _rebuild(published),
    }
