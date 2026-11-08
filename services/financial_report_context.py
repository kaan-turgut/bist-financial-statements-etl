"""Read a KAP attachment's stated presentation basis without inferring by year."""

import hashlib
import json
import re
import unicodedata
import zipfile

from unidecode import unidecode

from read_statements.eps import DATE, MONTHS, pdf_pages


def infer_context(pages, period_end):
    headers = [" ".join(page.splitlines()[:12]) for page in pages[:20]]
    normalized = [unidecode(h).lower() for h in headers]
    normalized = [
        re.sub(
            r"\bt\s+utarlar\b",
            "tutarlar",
            re.sub(r"\bt\s+urk\b", "turk", re.sub(r"\bt\s+l\b", "tl", text)),
        )
        for text in normalized
    ]
    dates = set()
    for text in normalized:
        for match in DATE.finditer(text):
            if match[3]:
                dates.add((int(match[3]), MONTHS[match[2]], int(match[1])))
        dates.update(
            (int(y), int(m), int(d))
            for d, m, y in re.findall(r"\b(\d{2})[./](\d{2})[./](\d{4})\b", text)
        )
    matching_period = (period_end.year, period_end.month, period_end.day) in dates
    presentation = [
        (h, n[n.index("tutarlar") :])
        for h, n in zip(headers, normalized)
        if "tutarlar" in n and ("turk lirasi" in n or re.search(r"\btl\b", n))
    ]
    if not matching_period or not presentation:
        return {
            "measurement_basis": "unknown",
            "purchasing_power_date": None,
            "evidence": {"reason": "no_matching_period_and_try_presentation_header"},
        }
    adjusted = [(h, n) for h, n in presentation if "satin alma gucu" in n]
    if adjusted:
        stated_dates = set()
        for _, text in adjusted:
            # Later paragraphs may name future effective dates for standards.
            # Only the date preceding the header's purchasing-power phrase
            # describes the measurement unit.
            matches = [
                m for m in DATE.finditer(text.split("satin alma gucu", 1)[0]) if m[3]
            ]
            if matches:
                m = matches[-1]
                stated_dates.add((int(m[3]), MONTHS[m[2]], int(m[1])))
        if stated_dates and max(stated_dates) != (
            period_end.year,
            period_end.month,
            period_end.day,
        ):
            return {
                "measurement_basis": "unknown",
                "purchasing_power_date": None,
                "evidence": {
                    "reason": "purchasing_power_date_does_not_match_report_period"
                },
            }
        return {
            "measurement_basis": "inflation_adjusted",
            "purchasing_power_date": period_end,
            "evidence": {"presentation_header": adjusted[0][0]},
        }
    return {
        "measurement_basis": "nominal",
        "purchasing_power_date": None,
        "evidence": {"presentation_header": presentation[0][0]},
    }


def read_report_context(path, quarter, period_end):
    archive, _ = str(path).split(".zip/", 1)
    candidates = []
    try:
        with zipfile.ZipFile(archive + ".zip") as zipped:
            members = [
                m
                for m in zipped.namelist()
                if m.lower().endswith(".pdf")
                and f"Dönem{quarter}Ekler/" in unicodedata.normalize("NFC", m)
            ]
            for member in members:
                name = unidecode(member).lower()
                if re.search(r"\b(?:ing|eng|english|ingilizce)\b", name):
                    continue
                data = zipped.read(member)
                context = infer_context(pdf_pages(data), period_end)
                if context["measurement_basis"] != "unknown":
                    context.update(
                        source_path=archive + ".zip/" + member,
                        source_sha256=hashlib.sha256(data).hexdigest(),
                    )
                    candidates.append(context)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {
            "measurement_basis": "unknown",
            "purchasing_power_date": None,
            "evidence": {"reason": str(exc)},
        }
    identities = {
        (c["measurement_basis"], c["purchasing_power_date"]) for c in candidates
    }
    if len(identities) == 1:
        return candidates[0]
    return {
        "measurement_basis": "unknown",
        "purchasing_power_date": None,
        "evidence": {"reason": "missing_or_conflicting_presentation_headers"},
    }


def store_context(conn, filing_key, run_id, context):
    conn.execute(
        """INSERT INTO silver.financial_report_context
        (filing_key,processing_run_id,measurement_basis,purchasing_power_date,source_path,source_sha256,evidence)
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
        (
            filing_key,
            run_id,
            context["measurement_basis"],
            context["purchasing_power_date"],
            context.get("source_path"),
            context.get("source_sha256"),
            json.dumps(context["evidence"], default=str),
        ),
    )
