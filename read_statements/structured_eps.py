"""Extract dated EPS cells from KAP HTML, including typed-dimension children.

This stage preserves reported amounts and dates without guessing currency,
nominal-share units, or presentation multipliers. Normalization is separate.
"""

import re
from datetime import datetime
from decimal import Decimal

from lxml import html
from lxml.etree import ParserError, XMLSyntaxError
from unidecode import unidecode

from models.earnings_per_share import EpsObservation
from read_statements.eps import (
    EPS_LABEL,
    NOTE,
    _eps_unit,
    _money_multiplier,
    _nominal_value,
    _values,
    parse_number,
)

DATES = re.compile(r"\d{2}\.\d{2}\.\d{4}")
ROLE = re.compile(r"(?:general|holding)_role_(.+)-row-(\d+)")


def text(element):
    return " ".join(" ".join(element.itertext()).split())


def has_class(element, name):
    return name in element.get("class", "").split()


def structured_eps_cells(data, fiscal_quarter, expected_end=None):
    """Return current-period cells plus complete source-column provenance."""
    try:
        root = html.fromstring(data, parser=html.HTMLParser(encoding="utf-8"))
    except (ParserError, XMLSyntaxError) as exc:
        raise ValueError("Unable to read KAP structured EPS HTML.") from exc
    cells, warnings = [], []
    for table in root.xpath(
        "//table[contains(concat(' ',normalize-space(@class),' '),' financial-table ')]"
    ):
        headers = table.xpath(
            ".//td[contains(concat(' ',normalize-space(@class),' '),' context-header ')]"
        )
        periods = []
        for header in headers:
            dates = DATES.findall(text(header))
            periods.append(
                tuple(datetime.strptime(d, "%d.%m.%Y").date() for d in dates)
                if len(dates) == 2
                else None
            )
        if not any(periods):
            continue
        report_end = expected_end or max(p[-1] for p in periods if p)
        parent_label, parent_id = None, None
        for row in table.xpath(
            ".//tr[td[contains(concat(' ',normalize-space(@class),' '),' taxonomy-context-value ')]]"
        ):
            # Only real data rows: nested tables used to render labels are not
            # financial records. Typed children have their own extra caption TD.
            values = row.xpath(
                "./td[contains(concat(' ',normalize-space(@class),' '),' taxonomy-context-value ')]"
            )
            if not values:
                continue
            title = row.xpath(
                "./td[contains(concat(' ',normalize-space(@class),' '),' taxonomy-field-title ')]"
            )
            typed = has_class(row, "new-type-row")
            caption = row.xpath(
                "./td//div[contains(concat(' ',normalize-space(@class),' '),' typed-dimension-field-caption ')]"
            )
            label = text(title[0]) if title else (text(caption[0]) if caption else "")
            match = ROLE.search(row.get("class", ""))
            source_id = f"{match[1]}:{match[2]}" if match else None
            if not typed:
                parent_label, parent_id = label, source_id
            if typed and not EPS_LABEL.search(unidecode(label).lower()):
                # A caption can identify a specific share class instead of
                # repeating the EPS label; don't silently call it ordinary.
                if parent_label and EPS_LABEL.search(unidecode(parent_label).lower()):
                    label = parent_label + " | " + label
                else:
                    continue
            normalized = unidecode(label).lower()
            if not EPS_LABEL.search(normalized):
                continue
            if len(values) != len(headers):
                warnings.append(
                    f"{source_id or parent_id}: EPS column count differs from date headers"
                )
                continue
            semantic_label = unidecode(
                (parent_label + " " + label) if typed and parent_label else label
            ).lower()
            if "surdurulen" in semantic_label and "durdurulan" in semantic_label:
                warnings.append(
                    f"{source_id or parent_id}: contradictory EPS operation scopes"
                )
                continue
            scope = (
                "continuing"
                if "surdurulen" in semantic_label
                else ("discontinued" if "durdurulan" in semantic_label else "total")
            )
            kind = (
                "diluted"
                if any(
                    word in semantic_label for word in ("sulandirilmis", "seyreltilmis")
                )
                else "basic"
            )
            share_class = "ordinary" if " | " not in label else label.split(" | ", 1)[1]
            for index, (value, period) in enumerate(zip(values, periods)):
                if not period or period[-1] != report_end:
                    continue
                raw = text(value)
                if not raw or raw in {"-", "–", "—"}:
                    continue
                try:
                    amount = parse_number(raw)
                except ValueError as exc:
                    warnings.append(
                        f"{source_id or parent_id} column {index + 1}: {exc}"
                    )
                    continue
                days = (period[1] - period[0]).days + 1
                period_kind = (
                    "quarter" if fiscal_quarter > 1 and 75 <= days <= 100 else "ytd"
                )
                if not (
                    75 <= days <= 100
                    if period_kind == "quarter"
                    else fiscal_quarter * 75 <= days <= fiscal_quarter * 100
                ):
                    warnings.append(
                        f"{source_id or parent_id}: EPS dates do not match fiscal quarter"
                    )
                    continue
                cells.append(
                    dict(
                        period_start=period[0],
                        period_end=period[1],
                        period_kind=period_kind,
                        eps_kind=kind,
                        share_class=share_class,
                        earnings_scope=scope,
                        raw_value=amount,
                        label=label,
                        parent_label=parent_label if typed else None,
                        source_row_id=source_id or parent_id,
                        typed_dimension=typed,
                        source_column_index=index + 1,
                        column_header=text(headers[index]),
                        all_column_values=[text(v) for v in values],
                    )
                )
    return cells, warnings


def observation_key(observation):
    return (
        observation.period_kind,
        observation.eps_kind,
        observation.share_class,
        observation.earnings_scope,
    )


def merge_eps_observations(observations):
    """Combine complementary sources, rejecting genuine disagreement."""
    selected = {}
    for observation in observations:
        key = observation_key(observation)
        previous = selected.get(key)
        if previous:
            if (
                (previous.period_start, previous.period_end)
                != (observation.period_start, observation.period_end)
                or previous.purchasing_power_date != observation.purchasing_power_date
                or abs(previous.value - observation.value)
                > previous.rounding_tolerance + observation.rounding_tolerance
            ):
                raise ValueError(f"EPS sources disagree for {key}")
            # Keep disclosed historical denominator/profit when another source
            # repeats only the rounded EPS value.
            if previous.has_inputs or not observation.has_inputs:
                continue
        selected[key] = observation
    return list(selected.values())


def normalize_structured_cells(cells, references):
    """Transfer only PDF-verified units anchored to matching reported amounts.

    Date selection comes from HTML. PDF references establish currency/nominal
    share scale; neither statement money multipliers nor current capital are
    used. A unit anchored to one column can apply to other columns of that
    same EPS row, preserving separate quarterly and YTD observations.
    """
    warnings = []
    anchors = {}
    for cell in cells:
        row_key = (cell["source_row_id"], cell["eps_kind"], cell["share_class"])
        for reference in references:
            raw = reference.normalization.get("raw_reported_eps")
            if raw is None or reference.share_class != cell["share_class"]:
                continue
            if (reference.period_start, reference.period_end) != (
                cell["period_start"],
                cell["period_end"],
            ):
                continue
            raw = Decimal(raw)
            quantum = Decimal(1).scaleb(raw.as_tuple().exponent)
            if abs(raw - cell["raw_value"]) > abs(quantum) / 2:
                continue
            nominal = Decimal(reference.normalization["share_nominal_value"])
            split = Decimal(reference.normalization["split_adjustment_factor"])
            scale = (
                (
                    Decimal("0.01")
                    if reference.normalization["eps_unit"] == "KURUS"
                    else Decimal(1)
                )
                / nominal
                / split
            )
            anchors.setdefault(row_key, []).append((scale, reference))
    normalized = []
    for cell in cells:
        if cell["earnings_scope"] == "discontinued":
            continue
        row_key = (cell["source_row_id"], cell["eps_kind"], cell["share_class"])
        candidates = anchors.get(row_key, [])
        if len({scale for scale, _ in candidates}) != 1:
            warnings.append(f"{row_key}: no single PDF-verified EPS unit scale")
            continue
        scale, reference = candidates[0]
        normalized.append(
            EpsObservation(
                period_start=cell["period_start"],
                period_end=cell["period_end"],
                period_kind=cell["period_kind"],
                eps_kind=cell["eps_kind"],
                share_class=cell["share_class"],
                earnings_scope=cell["earnings_scope"],
                reported_eps=cell["raw_value"] * scale,
                purchasing_power_date=reference.purchasing_power_date,
                rounding_tolerance=reference.rounding_tolerance,
                normalization={
                    **reference.normalization,
                    "raw_reported_eps": str(cell["raw_value"]),
                    "validation": "structured_dates_with_pdf_verified_units",
                    "source_row_id": cell["source_row_id"],
                    "source_column_index": cell["source_column_index"],
                    "column_header": cell["column_header"],
                    "typed_dimension": cell["typed_dimension"],
                    "parent_label": cell.get("parent_label"),
                },
            )
        )
    return normalized, warnings


def reconcile_structured_pdf(pages, cells):
    """Use HTML dates with PDF EPS-table unit/earnings/share reconciliation.

    Some PDF headers are split across physical lines and cannot be assigned
    dates safely. Unit evidence can still be established independently: every
    column of a disclosed weighted-share table must reconcile, and the HTML
    amount must match a reported PDF EPS amount. No PDF column order is used
    to select a reporting date or attach an unverified denominator.
    """
    candidates = {}
    for page_number, page in enumerate(pages, 1):
        lines = page.splitlines()
        headings = [
            i for i, line in enumerate(lines) if NOTE.search(unidecode(line).lower())
        ]
        for heading in headings:
            block = "\n".join(lines[heading:])
            next_note = re.search(
                r"\n\s*(?:(?:NOT|DİPNOT)\s+\d{1,2}\s*[-.:]|\d{1,2}[.]\s+|\d{1,2}\s{2,})[A-Za-zÇĞİÖŞÜçğıöşü]",
                block[1:],
            )
            if next_note:
                block = block[: next_note.start() + 1]
            normalized = unidecode(block).lower()
            if "agirlikli" not in normalized or "ortalama" not in normalized:
                continue
            try:
                nominal = _nominal_value(pages, block)
                money = _money_multiplier(page)
            except ValueError:
                continue
            local = block.splitlines()
            for i, line in enumerate(local):
                context = unidecode(" ".join(local[max(0, i - 2) : i + 1])).lower()
                if not EPS_LABEL.search(context):
                    continue
                for count in (4, 2):
                    try:
                        eps = _values(line, count)
                    except ValueError:
                        continue
                    if eps is None:
                        continue
                    share_rows = []
                    profit_rows = []
                    for j, other in enumerate(local):
                        label = unidecode(
                            " ".join(local[max(0, j - 2) : j + 1])
                        ).lower()
                        try:
                            values = _values(other, count)
                        except ValueError:
                            continue
                        if values is None:
                            continue
                        if ("agirlikli" in label and "ortalama" in label) or re.search(
                            r"hisselerin ortalama adedi|adi\s+hisse\s+senetleri|(?:hisse|pay)\s+(?:adedi|sayisi)",
                            unidecode(other).lower(),
                        ):
                            share_rows.append((values, label))
                        if re.search(
                            r"(?:donem|net)\s+kar(?:i|\b)", unidecode(other).lower()
                        ):
                            profit_rows.append(values)
                    for shares, share_label in share_rows:
                        if any(s is None or s <= 0 for s in shares):
                            continue
                        scales = (
                            [Decimal(1000000)]
                            if "milyon" in share_label
                            else [Decimal(1000)]
                            if re.search(r"\bbin\b", share_label)
                            else [Decimal(1)]
                            if "tam adet" in share_label
                            else sorted({Decimal(1), money})
                        )
                        explicit_unit = _eps_unit(
                            " ".join(local[max(0, i - 1) : i + 1])
                        )
                        units = [explicit_unit] if explicit_unit else ["TRY", "KURUS"]
                        for profit in profit_rows:
                            for shares_scale in scales:
                                for unit in units:
                                    scale = (
                                        Decimal(".01")
                                        if unit == "KURUS"
                                        else Decimal(1)
                                    ) / nominal
                                    valid = True
                                    for e, p, s in zip(eps, profit, shares):
                                        if e is None or p is None:
                                            valid = False
                                            break
                                        expected = (
                                            p * money / (s * shares_scale * nominal)
                                        )
                                        tolerance = (
                                            abs(
                                                Decimal(1).scaleb(e.as_tuple().exponent)
                                            )
                                            * scale
                                            * Decimal("1.01")
                                        )
                                        if abs(expected - e * scale) > tolerance:
                                            valid = False
                                            break
                                    if not valid:
                                        continue
                                    for cell in cells:
                                        if cell["earnings_scope"] == "discontinued":
                                            continue
                                        if not any(
                                            e is not None
                                            and abs(e - cell["raw_value"])
                                            <= abs(
                                                Decimal(1).scaleb(e.as_tuple().exponent)
                                            )
                                            / 2
                                            for e in eps
                                        ):
                                            continue
                                        key = (
                                            cell["source_row_id"],
                                            cell["eps_kind"],
                                            cell["share_class"],
                                        )
                                        candidates.setdefault(key, []).append(
                                            (
                                                scale,
                                                unit,
                                                nominal,
                                                shares_scale,
                                                money,
                                                page_number,
                                            )
                                        )
    observations = []
    warnings = []
    for cell in cells:
        if cell["earnings_scope"] == "discontinued":
            continue
        key = (cell["source_row_id"], cell["eps_kind"], cell["share_class"])
        matches = candidates.get(key, [])
        if len({m[0] for m in matches}) != 1:
            continue
        scale, unit, nominal, shares_scale, money, page_number = matches[0]
        observations.append(
            EpsObservation(
                period_start=cell["period_start"],
                period_end=cell["period_end"],
                period_kind=cell["period_kind"],
                eps_kind=cell["eps_kind"],
                share_class=cell["share_class"],
                earnings_scope=cell["earnings_scope"],
                reported_eps=cell["raw_value"] * scale,
                purchasing_power_date=cell["period_end"]
                if any("satin alma gucu" in unidecode(p).lower() for p in pages[:5])
                else None,
                rounding_tolerance=Decimal(".0051") * scale,
                normalization={
                    "validation": "structured_dates_with_reconciled_pdf_table",
                    "source_page": page_number,
                    "raw_reported_eps": str(cell["raw_value"]),
                    "eps_unit": unit,
                    "share_nominal_value": str(nominal),
                    "split_adjustment_factor": "1",
                    "money_multiplier": str(money),
                    "shares_multiplier": str(shares_scale),
                    "output_unit": "TRY_per_1_TRY_nominal",
                    "source_row_id": cell["source_row_id"],
                    "source_column_index": cell["source_column_index"],
                    "parent_label": cell.get("parent_label"),
                    "column_header": cell["column_header"],
                },
            )
        )
    return observations, warnings
