"""Recover explicitly scaled EPS and independent disclosed calculation inputs.

Positions belong to PDF text rendered with layout preserved. Empty cells are
retained against a complete numeric anchor row; currencies never share a slot.
"""

import re
from datetime import date
from decimal import Decimal

from unidecode import unidecode

from models.earnings_per_share import EpsObservation, normalize_observation
from read_statements.eps import (
    DATE,
    MONTHS,
    NUMBER,
    EPS_LABEL,
    _money_multiplier,
    _nominal_value,
    _eps_unit,
    _nominal_values,
    NOTE,
    parse_number,
)


def numeric_cells(line):
    matches = list(NUMBER.finditer(line))
    for i, match in enumerate(matches):
        if match.start() < 20 or not re.search(r"\s{2,}$", line[: match.start()]):
            continue
        if re.fullmatch(r"[\s\d.,()–—-]+", line[match.start() :]):
            try:
                return [(m.end(), parse_number(m[0])) for m in matches[i:]]
            except ValueError:
                return []
    return []


def aligned_values(line, positions):
    result = [None] * len(positions)
    for edge, value in numeric_cells(line):
        column = min(range(len(positions)), key=lambda i: abs(positions[i] - edge))
        distances = [
            abs(positions[column] - p) for p in positions if p != positions[column]
        ]
        tolerance = min([12, *(d / 3 for d in distances)])
        if abs(positions[column] - edge) > tolerance:
            continue
        if result[column] is not None:
            raise ValueError("Multiple PDF amounts map to one EPS column")
        result[column] = value
    return result


def layout_columns(lines, before):
    """Use complete numeric rows to recover 2/4/6-column date/currency headers."""
    candidates = []
    for i in range(before + 1):
        values = numeric_cells(lines[i])
        if len(values) in (2, 4, 6):
            candidates.append((len(values), i, [p for p, _ in values]))
    for _, anchor, positions in sorted(candidates, reverse=True):
        fragments = [[] for _ in positions]
        # Financial statement date headers can be far above the EPS row.
        for line in lines[max(0, anchor - 65) : anchor]:
            for match in re.finditer(r"\S(?:.*?\S)?(?=\s{2,}|$)", line):
                content = unidecode(match[0]).lower()
                if not (
                    DATE.search(content)
                    or re.fullmatch(r"(?:19|20)\d{2}", content)
                    or re.search(r"\b(?:tl|try|usd|abd dolari|avro|eur)\b", content)
                ):
                    continue
                edge = match.end()
                column = min(
                    range(len(positions)), key=lambda n: abs(positions[n] - edge)
                )
                if abs(positions[column] - edge) <= 16:
                    fragments[column].append(content)
        columns = []
        for parts in fragments:
            content = " ".join(parts)
            found = list(DATE.finditer(content))
            if len(found) < 2 or not found[-1][3]:
                break
            start, end = found[-2:]
            stop = date(int(end[3]), MONTHS[end[2]], int(end[1]))
            begin = date(
                int(start[3])
                if start[3]
                else stop.year - (MONTHS[start[2]] > stop.month),
                MONTHS[start[2]],
                int(start[1]),
            )
            if not 75 <= (stop - begin).days + 1 <= 366:
                break
            foreign = re.search(r"\b(?:usd|abd dolari|avro|eur)\b", content)
            columns.append((begin, stop, not foreign))
        if len(columns) == len(positions):
            return positions, columns
    raise ValueError("No complete PDF date/currency layout")


def per_nominal_unit(label):
    """Explicit EPS per stated nominal capital; no current share metadata."""
    normalized = unidecode(label).lower()
    found = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(tl|kr\.?|kurus)\s+nominal\s+(?:bedel|deger)", normalized
    )
    if not found:
        return None
    nominal = Decimal(found[1].replace(",", ".")) * (
        1 if found[2] == "tl" else Decimal(".01")
    )
    if nominal <= 0:
        return None
    # A declared TL-nominal EPS is in TRY unless a separate EPS currency says otherwise.
    unit = _eps_unit(label) or "TRY"
    return unit, nominal


def eps_note_block(lines, index):
    """An EPS-note nominal basis overrides legal-share units in capital notes."""
    headings = [i for i in range(index + 1) if NOTE.search(unidecode(lines[i]).lower())]
    if not headings or index - headings[-1] > 60:
        return ""
    start, stop = headings[-1], len(lines)
    for i in range(index + 1, len(lines)):
        if re.match(
            r"^\s*(?:(?:not|dipnot)\s+)?(?:\d{1,2}|[ivxlcdm]{1,8})[.\-:]\s+[a-z]",
            unidecode(lines[i]).lower(),
        ):
            stop = i
            break
    return "\n".join(lines[start:stop])


def extract_recoverable_eps(pages, fiscal_quarter, expected_end=None):
    observations, warnings = [], []
    for page_number, page in enumerate(pages, 1):
        lines = page.splitlines()
        normalized = [unidecode(line).lower() for line in lines]
        eps_indices = [
            i
            for i, line in enumerate(normalized)
            if EPS_LABEL.search(line) and numeric_cells(lines[i])
        ]
        for index in eps_indices:
            try:
                positions, columns = layout_columns(lines, index)
                current_end = expected_end or max(end for _, end, _ in columns)
                # Adjacent basic/diluted rows may use different EPS currencies.
                # A following nominal-value caption belongs to this row; the
                # next EPS row's unit does not.
                label_parts = [lines[index]]
                for following in lines[index + 1 : index + 3]:
                    if EPS_LABEL.search(unidecode(following).lower()):
                        break
                    label_parts.append(following)
                local_label = " ".join(label_parts)
                kind = (
                    "diluted"
                    if re.search(r"sulandirilmis|seyreltilmis", normalized[index])
                    else "basic"
                )
                scope = "continuing" if "surdurulen" in normalized[index] else "total"
                if "durdurulan" in normalized[index]:
                    continue
                values = aligned_values(lines[index], positions)
                unit = per_nominal_unit(local_label)
                if unit is None and _eps_unit(local_label):
                    note = eps_note_block(lines, index)
                    nominals = _nominal_values(note)
                    if len(nominals) == 1 and next(iter(nominals)) > 0:
                        unit = (_eps_unit(local_label), next(iter(nominals)))
                        local_label += "\n" + note
                power = (
                    current_end
                    if "satin alma gucu" in unidecode(page).lower()
                    else None
                )
                for column, (start, end, try_column) in enumerate(columns):
                    raw = values[column]
                    if raw is None or end != current_end or not try_column:
                        continue
                    period_kind = (
                        "quarter"
                        if fiscal_quarter > 1 and (end - start).days < 100
                        else "ytd"
                    )
                    if unit:
                        obs = normalize_observation(
                            period_start=start,
                            period_end=end,
                            period_kind=period_kind,
                            eps_kind=kind,
                            earnings_scope=scope,
                            reported_eps=raw,
                            eps_unit=unit[0],
                            share_nominal_value=unit[1],
                            rounding_tolerance=abs(
                                Decimal(1).scaleb(raw.as_tuple().exponent)
                            )
                            / 2,
                            purchasing_power_date=power,
                            normalization={
                                "validation": "explicit_nominal_pdf_layout",
                                "source_page": page_number,
                                "source_eps_label": lines[index].strip(),
                                "source_unit_evidence": local_label,
                                "source_column_index": column + 1,
                            },
                        )
                        observations.append(obs)
            except (ValueError, ArithmeticError) as exc:
                warnings.append(f"layout page {page_number}: {exc}")

        # Extract the numerator/denominator independently of reported EPS rows.
        # In particular a diluted-only row does not prevent basic calculation.
        weighted = [
            i
            for i, line in enumerate(normalized)
            if numeric_cells(lines[i])
            and re.search(
                r"agirlikli|hisselerin ortalama adedi",
                " ".join(normalized[max(0, i - 1) : i + 1]),
            )
            and re.search(r"adet|adedi|sayisi|ortalama", line)
        ]
        for index in weighted:
            try:
                positions, columns = layout_columns(lines, index)
                current_end = expected_end or max(end for _, end, _ in columns)
                vicinity = range(max(0, index - 12), min(len(lines), index + 16))
                profit_rows = [
                    i
                    for i in vicinity
                    if numeric_cells(lines[i])
                    and re.search(
                        r"ana ortakl[iı](?:ga|k).*?(?:kar|zarar)|ana ortaklik paylari",
                        normalized[i],
                    )
                    and "surdurulen" not in normalized[i]
                ]
                if len(profit_rows) != 1:
                    continue
                profits = aligned_values(lines[profit_rows[0]], positions)
                shares = aligned_values(lines[index], positions)
                label = " ".join(normalized[max(0, index - 1) : index + 1])
                if re.search(r"sulandirilmis|seyreltilmis", label):
                    # A diluted-only denominator cannot establish basic EPS.
                    continue
                shares_scale = (
                    Decimal(1000000)
                    if "milyon" in label
                    else Decimal(1000)
                    if re.search(r"\bbin\b", label)
                    else Decimal(1)
                    if re.search(r"tam (?:adet|deger)", label)
                    else None
                )
                if shares_scale is None:
                    continue
                nominal = _nominal_value(pages, page)
                money = _money_multiplier(page)
                power = (
                    current_end
                    if "satin alma gucu" in unidecode(page).lower()
                    else None
                )
                for column, (start, end, try_column) in enumerate(columns):
                    if (
                        end != current_end
                        or not try_column
                        or profits[column] is None
                        or not shares[column]
                    ):
                        continue
                    observations.append(
                        EpsObservation(
                            period_start=start,
                            period_end=end,
                            period_kind="quarter"
                            if fiscal_quarter > 1 and (end - start).days < 100
                            else "ytd",
                            attributable_profit=profits[column] * money,
                            weighted_average_shares=shares[column]
                            * shares_scale
                            * nominal,
                            purchasing_power_date=power,
                            normalization={
                                "validation": "disclosed_basic_inputs",
                                "source_page": page_number,
                                "profit_label": lines[profit_rows[0]].strip(),
                                "shares_label": lines[index].strip(),
                                "share_nominal_value": str(nominal),
                                "shares_multiplier": str(shares_scale),
                                "money_multiplier": str(money),
                                "split_adjustment_factor": "1",
                                "eps_unit": "TRY",
                                "source_column_index": column + 1,
                            },
                        )
                    )
            except (ValueError, ArithmeticError) as exc:
                warnings.append(f"inputs page {page_number}: {exc}")
    return observations, warnings
