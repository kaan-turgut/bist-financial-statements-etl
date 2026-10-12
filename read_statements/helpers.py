import json
import re
from functools import lru_cache

import pandas as pd
import unidecode

from paths import DATA_MAPPINGS_DIR


def parse_reporting_info(soup):
    """Extract reporting year, period, reporting unit multipliers, and report kind (consolidated, non-consolidated, etc.)"""
    div_text = str(soup.find_all("div"))

    # single regex scans instead of multiple soup traversals
    reporting_year_match = re.search(r"Yıl:(\d{4})", div_text)
    reporting_period_match = re.search(r"Periyot:(\d{1})", div_text)

    reporting_year = reporting_year_match.group(1) if reporting_year_match else None
    reporting_period = reporting_period_match.group(1) if reporting_period_match else None

    report_kind = re.findall(
    r"Finansal Tablo Niteliği</td>\s*<td>(Konsolide Olmayan|Konsolide)",
    div_text,
    re.IGNORECASE | re.DOTALL
    )

    reporting_unit = re.findall(
    r"Sunum Para Birimi</td>\s*<td>(.*?)\s*TL</td>",
    div_text,
    re.IGNORECASE | re.DOTALL
    )
    # normalize once here
    reporting_unit = [
    1 if u.strip() == "" else float(u.replace(".", ""))
    for u in reporting_unit
    ]

    return reporting_year, reporting_period, reporting_unit, report_kind


def classify_texts(labels):

    """Classify raw texts into date, number, or item using regex."""
    date_pattern = re.compile(r"\d{2}\.\d{2}\.\d{4}\s*\-\s*\d{2}\.\d{2}\.\d{4}|\d{2}\.\d{2}\.\d{4}")
    number_pattern = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^-?\d+(?:,\d+)?$")

    statement_types, sections, hierarchy = load_statement_mappings()
    statement_types_tr = set(statement_types.keys())

    classified = []
    for raw in labels:
        text = raw.strip()

        if date_pattern.search(text):
            classified.append(("date", date_pattern.search(text).group(), text))

        elif number_pattern.match(text):
            num = int(text.replace(".", "").replace(",", "."))
            classified.append(("number", num, text))

        elif text in statement_types_tr:
            classified.append(("tableName", statement_types[text], text))

        elif unidecode.unidecode(text).lower() in sections:
            classified.append(("section", sections[unidecode.unidecode(text).lower()], text))

        elif unidecode.unidecode(text).lower() in hierarchy:
            classified.append(("parent", hierarchy[unidecode.unidecode(text).lower()], text))

        else:
            classified.append(("item", text, text))

    return classified


@lru_cache(maxsize=1)
def load_statement_mappings():
    with open(DATA_MAPPINGS_DIR / "statement_types.json", "r", encoding="utf-8") as f:
        statement_types = json.load(f)

    with open(DATA_MAPPINGS_DIR / "statement_sections.json", "r", encoding="utf-8") as f:
        sections = json.load(f)

    with open(DATA_MAPPINGS_DIR / "statement_hierarchy.json", "r", encoding="utf-8") as f:
        hierarchy = json.load(f)

    return statement_types, sections, hierarchy


def turkish_lower(text):
    mapping = str.maketrans({
        'I': 'ı',
        'İ': 'i',
        'Ş': 'ş',
        'Ç': 'ç',
        'Ö': 'ö',
        'Ü': 'ü',
        'Ğ': 'ğ',
    })
    return text.translate(mapping).lower()


def parse_interval(date_str):
    if " - " in date_str:  # interval
        start_str, end_str = date_str.split(" - ")
        start = pd.to_datetime(start_str, format="%d.%m.%Y")
        end = pd.to_datetime(end_str, format="%d.%m.%Y")
        return (end - start).days
    return None  # single date


def parse_date_year(date_str):
    if " - " in date_str:  # interval
        single_date = date_str.split(" - ")[0]
        single_date = pd.to_datetime(single_date, format="%d.%m.%Y")
    else:
        single_date = pd.to_datetime(date_str, format="%d.%m.%Y")
    return int(single_date.year)
