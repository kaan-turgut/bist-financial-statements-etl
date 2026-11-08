"""Conservative extraction of reported EPS from Turkish KAP PDF tables.

Unknown units, ambiguous nominal share values, or inconsistent calculations are
rejected rather than filled using today's company metadata.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import date
from decimal import Decimal
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from unidecode import unidecode

from models.earnings_per_share import normalize_observation

MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "ocak",
            "subat",
            "mart",
            "nisan",
            "mayis",
            "haziran",
            "temmuz",
            "agustos",
            "eylul",
            "ekim",
            "kasim",
            "aralik",
        ),
        1,
    )
}
DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?\b")
NUMBER = re.compile(r"\(\s*-?\d[\d.,]*\s*\)|-?\d[\d.,]*|(?<!\w)[-–—](?!\w)")
EPS_LABEL = re.compile(
    r"(?:pay|hisse)\s+basina\b[^\n]{0,45}?(?:kazanc|kar|zarar|kayip)"
)
NOTE = re.compile(
    r"^\s*(?:(?:not|dipnot)\s+)?(?:\d{1,2}|[ivxlcdm]{1,8})(?:\s*[.\-–:]\s*|\s+)(?:pay|hisse)\s+basina\s+(?:kazanc|kar)",
    re.MULTILINE,
)


def parse_number(text):
    original = text
    text = text.strip()
    if text in {"-", "–", "—"}:
        return None
    negative = text.startswith("(")
    if negative:
        if not text.endswith(")"):
            raise ValueError(f"Invalid EPS number: {original!r}")
        text = text[1:-1].strip()
    # PDF text can include dotted leaders, repeated separators, or damaged
    # cells. Reject the whole token rather than guessing a financial amount.
    if not re.fullmatch(r"-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?|-?\d+\.\d+", text):
        raise ValueError(f"Invalid EPS number: {original!r}")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    result = Decimal(text)
    return -abs(result) if negative else result


def pdf_pages(data, *, ocr=False):
    # Some KAP exports wrap a PDF in a Java-serialization byte-array header.
    offset = data.find(b"%PDF-")
    if offset < 0:
        raise ValueError("EPS attachment is not a PDF.")
    data = data[offset:]
    if shutil.which("pdftotext"):
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", "-", "-"],
                input=data,
                capture_output=True,
                check=True,
                timeout=90,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError("Unable to extract EPS attachment text.") from exc
        pages = result.stdout.decode("utf-8").split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        if ocr:
            pages = ocr_empty_pages(data, pages)
        return pages
    try:
        pages = [
            page.extract_text(extraction_mode="layout") or ""
            for page in PdfReader(BytesIO(data)).pages
        ]
        return ocr_empty_pages(data, pages) if ocr else pages
    except PdfReadError as exc:
        raise ValueError("Unable to read EPS attachment PDF.") from exc


def ocr_empty_pages(data, pages):
    """Local opt-in OCR for image-only pages; preserve table spacing with psm 6."""
    try:
        return _ocr_empty_pages(data, pages)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Unable to OCR EPS attachment pages.") from exc


def _ocr_empty_pages(data, pages):
    empty = [i for i, p in enumerate(pages) if len(re.sub(r"\s+", "", p)) < 40]
    if not empty:
        return pages
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        raise ValueError(
            "Image-only EPS pages require pdftoppm and tesseract (Turkish language data recommended)."
        )
    with tempfile.TemporaryDirectory(prefix="bist-eps-ocr-") as work:
        pdf = Path(work) / "report.pdf"
        pdf.write_bytes(data)
        languages = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.splitlines()
        language = (
            "tur+eng"
            if "tur" in languages and "eng" in languages
            else "tur"
            if "tur" in languages
            else "eng"
        )
        for i in empty:
            target = str(Path(work) / f"page-{i + 1}")
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(i + 1),
                    "-l",
                    str(i + 1),
                    "-scale-to",
                    "2400",
                    "-singlefile",
                    "-png",
                    str(pdf),
                    target,
                ],
                capture_output=True,
                check=True,
                timeout=90,
            )
            result = subprocess.run(
                [
                    "tesseract",
                    target + ".png",
                    "stdout",
                    "-l",
                    language,
                    "--psm",
                    "6",
                    "-c",
                    "preserve_interword_spaces=1",
                ],
                capture_output=True,
                check=True,
                timeout=90,
            )
            pages[i] = result.stdout.decode("utf-8")
    return pages


def _nominal_values(text):
    normalized = unidecode(text).lower()
    values = set()
    pattern = r"(?<![\d.,])(\d+(?:[,.]\d+)?)\s*(?:tam\s+)?(tl|kr\.?|krs\.?|kurus)\s*(?:nominal|itibari)\s*(?:deger|bedel)"
    for amount, unit in re.findall(pattern, normalized):
        value = Decimal(amount.replace(",", "."))
        values.add(value if unit == "tl" else value / 100)
    pattern = (
        r"nominal\s*(?:pay\s+)?degeri\s*(\d+(?:[,.]\d+)?)\s*(?:tam\s+)?(tl|kr\.?|kurus)"
    )
    for amount, unit in re.findall(pattern, normalized):
        value = Decimal(amount.replace(",", "."))
        values.add(value if unit == "tl" else value / 100)
    # Explicit "each share" declarations are also found without the words
    # nominal/itibari (for example "her biri 1 krş").
    for amount, unit in re.findall(
        r"her\s+biri\s+(\d+(?:[,.]\d+)?)\s*(tl|krs\.?|kurus|kr\.?)\b", normalized
    ):
        value = Decimal(amount.replace(",", "."))
        values.add(value if unit == "tl" else value / 100)
    return values


def _nominal_value(pages, local_text):
    values = _nominal_values(local_text)
    if not values:
        capital_pages = [
            p
            for p in pages
            if re.search(
                r"^\s*(?:(?:not|dipnot)\s+)?(?:\d{1,2}|[ivxlcdm]{1,8})(?:\s*[.\-–:]\s*|\s+)(?:ozkaynaklar|sermaye|ana ortaklik bankanin sermaye)",
                unidecode(p).lower(),
                re.MULTILINE,
            )
        ]
        values = _nominal_values("\n".join(capital_pages))
    if not values:
        declarations = []
        for page in pages:
            lines = page.splitlines()
            for i, line in enumerate(lines):
                local = " ".join(lines[max(0, i - 1) : i + 2])
                normalized = unidecode(local).lower()
                if re.search(
                    r"\b(?:beher|beheri|her biri|birim nominal pay)\b", normalized
                ) and re.search(r"\b(?:pay|hisse)", normalized):
                    declarations.append(local)
        values = _nominal_values("\n".join(declarations))
    if len(values) != 1:
        raise ValueError(
            "EPS source does not establish one unambiguous nominal share value."
        )
    value = values.pop()
    if value <= 0:
        raise ValueError("EPS nominal share value must be positive.")
    return value


def _money_multiplier(page):
    header = unidecode(" ".join(page.splitlines()[:12])).lower()
    if "turk lirasi" not in header and not re.search(r"\btl\b", header):
        raise ValueError("EPS earnings are not explicitly presented in TRY.")
    if re.search(r"\bmilyon\s+(?:turk lirasi|[\"'(]*tl\b)", header):
        return Decimal(1000000)
    if re.search(r"\bbin\s+(?:turk lirasi|[\"'(]*tl\b)", header):
        return Decimal(1000)
    if "tutarlar" in header and ("turk lirasi" in header or '"tl"' in header):
        return Decimal(1)
    raise ValueError("Unknown EPS earnings presentation unit.")


def _columns(lines, before):
    """Resolve each column's full period instead of assuming column ordering."""
    # Reassemble wrapped header cells by their horizontal position. PDFs can
    # place "1 Şubat – 31" above "Temmuz 2026", while HTML places that period
    # in one cell. Numeric-column right edges anchor each independent header.
    for count in (4, 2):
        if _values(lines[before], count) is None:
            continue
        positions = [m.end() for m in list(NUMBER.finditer(lines[before]))[-count:]]
        fragments = [[] for _ in positions]
        for line in lines[max(0, before - 9) : before]:
            for chunk in re.finditer(r"\S(?:.*?\S)?(?=\s{2,}|$)", line):
                value = unidecode(chunk[0]).lower()
                if not (
                    any(month in value for month in MONTHS)
                    or re.fullmatch(r"(?:19|20)\d{2}", value)
                ):
                    continue
                centre = (chunk.start() + chunk.end()) / 2
                column = min(range(count), key=lambda n: abs(positions[n] - centre))
                if abs(positions[column] - centre) <= 22:
                    fragments[column].append(value)
        periods = []
        for parts in fragments:
            dates = list(DATE.finditer(" ".join(parts)))
            if len(dates) < 2 or not dates[-1][3]:
                break
            start, end = dates[-2:]
            end_date = date(int(end[3]), MONTHS[end[2]], int(end[1]))
            start_year = (
                int(start[3])
                if start[3]
                else end_date.year - (MONTHS[start[2]] > end_date.month)
            )
            start_date = date(start_year, MONTHS[start[2]], int(start[1]))
            if not 75 <= (end_date - start_date).days + 1 <= 366:
                break
            periods.append((start_date, end_date))
        if len(periods) == count:
            return periods
    for i in range(before - 1, -1, -1):
        matches = list(DATE.finditer(unidecode(lines[i]).lower()))
        # A separate year row can apply to N end-date columns (not N/2
        # start/end pairs). Preserve each column's order before considering
        # compact paired-date layouts.
        if len(matches) in {2, 4} and not any(m[3] for m in matches) and i + 1 < before:
            years = _values(lines[i + 1], len(matches))
            if years and all(y is not None and 1900 <= y <= 2100 for y in years):
                for j in range(i - 1, max(-1, i - 6), -1):
                    starts = list(DATE.finditer(unidecode(lines[j]).lower()))
                    if len(starts) == len(matches):
                        periods = []
                        for start, end, year in zip(starts, matches, years):
                            end_date = date(int(year), MONTHS[end[2]], int(end[1]))
                            start_year = (
                                int(start[3])
                                if start[3]
                                else int(year) - (MONTHS[start[2]] > end_date.month)
                            )
                            periods.append(
                                (
                                    date(start_year, MONTHS[start[2]], int(start[1])),
                                    end_date,
                                )
                            )
                        if all(
                            75 <= (end - start).days + 1 <= 366
                            for start, end in periods
                        ):
                            return periods
        # Some reports print both dates on one line and their years below it.
        if len(matches) in {4, 8} and not any(m[3] for m in matches) and i + 1 < before:
            years = _values(lines[i + 1], len(matches) // 2)
            if years and all(y is not None and 1900 <= y <= 2100 for y in years):
                periods = []
                for start, end, year in zip(matches[::2], matches[1::2], years):
                    end_date = date(int(year), MONTHS[end[2]], int(end[1]))
                    start_year = int(year) - (MONTHS[start[2]] > end_date.month)
                    periods.append(
                        (date(start_year, MONTHS[start[2]], int(start[1])), end_date)
                    )
                return periods
        ends = [m for m in matches if m[3]]
        if len(ends) not in {2, 4}:
            continue
        starts = []
        for j in range(i - 1, max(-1, i - 6), -1):
            found = list(DATE.finditer(unidecode(lines[j]).lower()))
            if len(found) == len(ends):
                starts = found
                break
        if not starts:
            continue
        periods = []
        for start, end in zip(starts, ends):
            end_date = date(int(end[3]), MONTHS[end[2]], int(end[1]))
            start_year = (
                int(start[3])
                if start[3]
                else end_date.year - (MONTHS[start[2]] > end_date.month)
            )
            periods.append(
                (date(start_year, MONTHS[start[2]], int(start[1])), end_date)
            )
        return periods
    raise ValueError("EPS table has no unambiguous period headers.")


def _values(line, count):
    matches = list(NUMBER.finditer(line))
    if len(matches) < count:
        return None
    if not re.fullmatch(r"[\s\d.,()–—-]+", line[matches[-count].start() :]):
        return None
    return [parse_number(match[0]) for match in matches[-count:]]


def _eps_unit(text):
    normalized = unidecode(text).lower()
    if re.search(r"\(\s*(?:tam\s+)?(?:kr\.?|kurus)\s*\)", normalized):
        return "KURUS"
    if re.search(r"\(\s*(?:tam\s+)?tl\s*\)", normalized):
        return "TRY"
    return None


def extract_pdf_eps(data, fiscal_year, fiscal_quarter, *, pages=None):
    pages = pdf_pages(data) if pages is None else pages
    observations = {}
    excerpts = []
    errors = []
    # First recover the EPS note and its disclosed calculation inputs.
    for page_number, page in enumerate(pages, 1):
        heading = next(
            (
                i
                for i, line in enumerate(page.splitlines())
                if NOTE.search(unidecode(line).lower())
            ),
            None,
        )
        if heading is None:
            continue
        try:
            block = "\n".join(page.splitlines()[heading:])
            next_note = re.search(r"\n\s*\d{1,2}\.\s+[A-Za-zÇĞİÖŞÜçğıöşü]", block[1:])
            if next_note:
                block = block[: next_note.start() + 1]
            lines = block.splitlines()
            weighted_note = (
                "agirlikli" in unidecode(block).lower()
                and "ortalama" in unidecode(block).lower()
            )
            share_row = next(
                i
                for i, line in enumerate(lines)
                if (
                    (
                        "agirlikli"
                        in unidecode(" ".join(lines[max(0, i - 2) : i + 1])).lower()
                        or "hisselerin ortalama adedi" in unidecode(line).lower()
                    )
                    and any(
                        word in unidecode(line).lower()
                        for word in ("adet", "adedi", "sayisi", "ortalama", "miktari")
                    )
                    and _values(line, 2)
                )
                or (
                    weighted_note
                    and re.search(
                        r"adi\s+(?:hisse\s+senetleri|paylar)|(?:hisse|pay)\s+(?:adedi|sayisi)",
                        unidecode(line).lower(),
                    )
                    and _values(line, 2)
                )
            )
            periods = _columns(lines, share_row)
            count = len(periods)
            shares = _values(lines[share_row], count)
            profit_candidates = [
                i
                for i, line in enumerate(lines)
                if re.search(r"(?:donem|net).*?(?:kar|zarar)", unidecode(line).lower())
                and _values(line, count)
                and "kontrol gucu" not in unidecode(line).lower()
                and "surdurulen" not in unidecode(line).lower()
            ]
            parent_profit = [
                i
                for i in profit_candidates
                if "ana ortak" in unidecode(lines[i]).lower()
            ]
            profit_row = next(iter(parent_profit or profit_candidates))
            profit = _values(lines[profit_row], count)
            basic_row = next(
                (
                    i
                    for i in range(profit_row + 1, len(lines))
                    if EPS_LABEL.search(
                        unidecode(
                            " ".join(lines[max(profit_row + 1, i - 1) : i + 1])
                        ).lower()
                    )
                    and _values(lines[i], count)
                ),
                None,
            )
            if basic_row is None:
                basic_row = next(
                    i
                    for i in range(profit_row + 1, len(lines))
                    if re.fullmatch(r"[\s\d.,()–—-]+", lines[i])
                    and _values(lines[i], count)
                )
            basic = _values(lines[basic_row], count)
            if shares is None or profit is None or basic is None:
                raise ValueError("EPS table contains incomplete calculation inputs.")
            nominal = _nominal_value(pages, block)
            money = _money_multiplier(page)
            share_label = unidecode(
                " ".join(lines[max(0, share_row - 2) : share_row + 1])
            ).lower()
            share_scales = (
                [Decimal(1000000)]
                if "milyon" in share_label
                else [Decimal(1000)]
                if re.search(r"\bbin\b", share_label)
                else [Decimal(1)]
                if "tam adet" in share_label
                else sorted({Decimal(1), money})
            )
            unit = _eps_unit(lines[basic_row])
            possible = []
            for shares_scale in share_scales:
                for candidate in [unit] if unit else ["TRY", "KURUS"]:
                    scale = Decimal(".01") if candidate == "KURUS" else Decimal(1)
                    comparable = [
                        (s, p, b)
                        for s, p, b in zip(shares, profit, basic)
                        if s and p is not None and b is not None
                    ]
                    if comparable and all(
                        abs(p * money / (s * shares_scale) - b * scale)
                        <= abs(Decimal(1).scaleb(b.as_tuple().exponent))
                        * Decimal("1.01")
                        * scale
                        for s, p, b in comparable
                    ):
                        possible.append((shares_scale, candidate))
            if len(possible) != 1:
                if unit is not None and not possible:
                    raise ValueError(
                        "Reported EPS does not reconcile with disclosed profit and shares."
                    )
                raise ValueError("EPS currency/share-count unit is ambiguous.")
            shares_scale, unit = possible[0]
            eps_label = lines[basic_row]
            if not EPS_LABEL.search(unidecode(eps_label).lower()):
                eps_label = " ".join(
                    lines[max(profit_row + 1, basic_row - 1) : basic_row + 1]
                )
            label_normalized = unidecode(eps_label).lower()
            scope = "continuing" if "surdurulen" in label_normalized else "total"
            if "durdurulan" in label_normalized:
                continue
            diluted_label = any(
                word in label_normalized for word in ("sulandirilmis", "seyreltilmis")
            )
            basic_label = any(
                word in label_normalized for word in ("adi/", "temel", "basit")
            )
            kinds = (
                ["basic", "diluted"]
                if diluted_label and basic_label
                else ["diluted"]
                if diluted_label
                else ["basic"]
            )
            power_date = (
                max(end for _, end in periods)
                if "satin alma gucu" in unidecode(page).lower()
                else None
            )
            for index, (start, end) in enumerate(periods):
                if end != max(end for _, end in periods):
                    continue
                if basic[index] is None:
                    continue
                period_kind = (
                    "quarter"
                    if fiscal_quarter > 1 and (end - start).days < 100
                    else "ytd"
                )
                quantum = Decimal(1).scaleb(basic[index].as_tuple().exponent)
                observation = normalize_observation(
                    period_start=start,
                    period_end=end,
                    period_kind=period_kind,
                    eps_kind=kinds[0],
                    earnings_scope=scope,
                    reported_eps=basic[index],
                    attributable_profit=profit[index],
                    weighted_average_shares=shares[index],
                    eps_unit=unit,
                    money_multiplier=money,
                    shares_multiplier=shares_scale,
                    share_nominal_value=nominal,
                    rounding_tolerance=quantum * Decimal("1.01"),
                    purchasing_power_date=power_date,
                    normalization={
                        "source_page": page_number,
                        "validation": "reconciled_note",
                        "source_eps_label": eps_label.strip(),
                    },
                )
                for kind in kinds:
                    selected = (
                        observation
                        if kind == observation.eps_kind
                        else type(observation)(
                            **{**vars(observation), "eps_kind": kind}
                        )
                    )
                    observations[(period_kind, kind, scope)] = selected
            excerpts.append({"page": page_number, "text": block})
        except (ValueError, StopIteration, ArithmeticError) as exc:
            errors.append(f"page {page_number}: {exc}")

    # Face statements can disclose standalone quarterly EPS even where the note
    # contains only YTD weighted-average shares. Never subtract reported EPS.
    for page_number, page in enumerate(pages[:20], 1):
        if "icindekiler" in unidecode(page).lower():
            continue
        lines = page.splitlines()
        for i, line in enumerate(lines):
            normalized = unidecode(line).lower()
            if not EPS_LABEL.search(normalized):
                continue
            try:
                if not _values(line, 2):
                    continue
                periods = _columns(lines, i)
                values = _values(line, len(periods))
                unit = _eps_unit(" ".join(lines[max(0, i - 2) : i + 1]))
                if unit is None or values is None:
                    continue
                nominal = _nominal_value(pages, " ".join(lines[max(0, i - 2) : i + 1]))
                kind = "diluted" if "sulandirilmis" in normalized else "basic"
                scope = (
                    "continuing"
                    if "surdurulen"
                    in unidecode(" ".join(lines[max(0, i - 2) : i + 1])).lower()
                    else "total"
                )
                power_date = (
                    max(end for _, end in periods)
                    if "satin alma gucu" in unidecode(page).lower()
                    else None
                )
                for value, (start, end) in zip(values, periods):
                    if value is None or end != max(e for _, e in periods):
                        continue
                    period_kind = "quarter" if (end - start).days < 100 else "ytd"
                    if fiscal_quarter == 1:
                        period_kind = "ytd"
                    key = (period_kind, kind, scope)
                    obs = normalize_observation(
                        period_start=start,
                        period_end=end,
                        period_kind=period_kind,
                        eps_kind=kind,
                        earnings_scope=scope,
                        reported_eps=value,
                        eps_unit=unit,
                        share_nominal_value=nominal,
                        rounding_tolerance=Decimal("0.0051"),
                        purchasing_power_date=power_date,
                        normalization={
                            "source_page": page_number,
                            "validation": "reported_face",
                        },
                    )
                    previous = observations.get(key)
                    if (
                        previous
                        and abs(previous.value - obs.value)
                        > previous.rounding_tolerance + obs.rounding_tolerance
                    ):
                        raise ValueError("EPS face and note values disagree.")
                    observations.setdefault(key, obs)
            except (ValueError, ArithmeticError) as exc:
                errors.append(f"face page {page_number}: {exc}")
    if any(
        "does not reconcile" in error or "values disagree" in error for error in errors
    ):
        raise ValueError("EPS source failed reconciliation: " + "; ".join(errors))
    return list(observations.values()), {
        "excerpts": excerpts,
        "extraction_warnings": errors,
    }
