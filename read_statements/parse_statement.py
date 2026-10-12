import re
import zipfile
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from models.financial_statement import FinancialStatement
from read_statements.helpers import classify_texts, parse_reporting_info, turkish_lower


def parse_statement(file_path, company):
    # split into the actual zip file path and the file inside it
    zip_path, inner_file = file_path.split(".zip/")
    zip_path = zip_path + ".zip"
    source_filename = Path(inner_file).name
    filing_id_match = re.search(r"_(\d+)_\d{4}_\d\.xls$", source_filename)
    filing_id = int(filing_id_match.group(1)) if filing_id_match else None

    # open the zip and read the inner .xls as text
    with zipfile.ZipFile(zip_path, "r") as z, z.open(inner_file) as f:
        html_content = f.read().decode("utf-8", errors="ignore")

    soup = BeautifulSoup(html_content, "html.parser")

    # strip empty tags in one pass
    for tag in soup.find_all():
        if (
            not tag.get_text(strip=True)
            and "taxonomy-dimensional-header-abstract-cell"
            not in (tag.get("class", []) if tag.attrs is not None else [])
            and "taxonomy-context-value"
            not in (tag.get("class", []) if tag.attrs is not None else [])
        ):
            tag.decompose()

    # extract reporting info
    reporting_year, reporting_period, reporting_unit, report_kind = parse_reporting_info(soup)

    # extract labels only once
    labels = soup.find_all("div", class_=["gwt-HTML multi-language-content content-tr", "gwt-Label"])
    clean_texts = [label.get_text(strip=True) for label in labels]

    # classify
    classified_data = classify_texts(clean_texts)

    column_headers = {}
    column_parents = {}
    positional_column_labels = {}
    for financial_table in soup.select("table.financial-table"):
        headers = {}
        parents = {}
        for cell in financial_table.select(
            "td.taxonomy-dimensional-header-cell, td.taxonomy-dimensional-header-abstract-cell"
        ):
            text = " ".join(cell.get_text(" ", strip=True).split())
            for class_name in cell.get("class", []):
                header_match = re.fullmatch(r"(?:col|span)-index-(\d+)", class_name)
                parent_match = re.fullmatch(r"col-abstract-index-(\d+)", class_name)
                if header_match and text:
                    headers[header_match.group(1)] = text
                if parent_match:
                    parent_class = next(
                        (name for name in cell.get("class", [])
                         if re.fullmatch(r"abstract-parent-(\d+)", name)),
                        None,
                    )
                    if parent_class:
                        parents[parent_match.group(1)] = parent_class.rsplit("-", 1)[1]
        column_headers[id(financial_table)] = headers
        column_parents[id(financial_table)] = parents

        positions = {}
        row_spans = {}
        for table_row in financial_table.find_all("tr"):
            if table_row.find_parent("table") is not financial_table:
                continue
            row_classes = table_row.get("class", [])
            if "data-input-row" in row_classes:
                break
            cells = table_row.find_all("td", recursive=False)
            if not any(
                {"taxonomy-dimensional-header-cell", "taxonomy-dimensional-header-abstract-cell"}
                & set(cell.get("class", []))
                for cell in cells
            ):
                continue

            occupied = {column for column, remaining in row_spans.items() if remaining > 0}
            column = 0
            for cell in cells:
                classes = set(cell.get("class", []))
                if not classes.intersection({
                    "taxonomy-dimensional-header-cell",
                    "taxonomy-dimensional-header-abstract-cell",
                }):
                    width = max(int(cell.get("colspan", 1) or 1), 1)
                    column += width
                    continue

                width = max(int(cell.get("colspan", 1) or 1), 1)
                while any(position in occupied for position in range(column, column + width)):
                    column += 1
                text = " ".join(cell.get_text(" ", strip=True).split())
                rowspan = max(int(cell.get("rowspan", 1) or 1), 1)
                for position in range(column, column + width):
                    if text:
                        positions.setdefault(position, []).append(text)
                    if rowspan > 1:
                        row_spans[position] = rowspan
                column += width

            row_spans = {
                position: remaining - 1
                for position, remaining in row_spans.items()
                if remaining > 1
            }

        positional_column_labels[id(financial_table)] = {
            str(position + 1): " > ".join(dict.fromkeys(labels))
            for position, labels in positions.items()
        }

    def resolve_column_label(financial_table, column_id):
        headers = column_headers.get(id(financial_table), {})
        parents = column_parents.get(id(financial_table), {})
        visited = set()
        current_id = column_id
        while current_id and current_id not in visited:
            if current_id in headers:
                return headers[current_id]
            visited.add(current_id)
            current_id = parents.get(current_id)
        return f"KAP column {column_id}" if column_id else None

    def get_source_row_metadata(label):
        is_abstract = False
        source_column_id = None
        column_position = None
        value_cell = label.find_parent("td", class_="taxonomy-context-value")
        if value_cell:
            value_cells = value_cell.find_parent("tr").find_all(
                "td", class_="taxonomy-context-value", recursive=False
            )
            column_position = value_cells.index(value_cell)
            for class_name in value_cell.get("class", []):
                column_match = re.fullmatch(r"col-order-class-(\d+)", class_name)
                if column_match:
                    source_column_id = column_match.group(1)
                    break
        else:
            header_cell = label.find_parent("td", class_="context-header")
            if header_cell:
                header_cells = header_cell.find_parent("tr").find_all(
                    "td", class_="context-header", recursive=False
                )
                column_position = header_cells.index(header_cell)

        for row in label.parents:
            if row.name != "tr":
                continue
            row_classes = row.get("class", [])
            is_abstract = is_abstract or "abstract-row" in row_classes
            for class_name in row_classes:
                match = re.fullmatch(r"(general|holding)_role_(.+)-row-(\d+)", class_name)
                if match:
                    role_prefix, taxonomy_id, row_number = match.groups()
                    source_row_id = f"{taxonomy_id}:{row_number}"
                    if role_prefix == "holding":
                        source_row_id = f"holding:{source_row_id}"
                    financial_table = row.find_parent("table", class_="financial-table")
                    if value_cell and source_column_id is None:
                        value_cells = row.find_all(
                            "td", class_="taxonomy-context-value", recursive=False
                        )
                        if value_cell in value_cells:
                            source_column_id = f"position-{value_cells.index(value_cell) + 1}"
                    column_label = resolve_column_label(financial_table, source_column_id)
                    if source_column_id and source_column_id.startswith("position-"):
                        position = source_column_id.removeprefix("position-")
                        column_label = positional_column_labels.get(id(financial_table), {}).get(
                            position, f"KAP value column {position}"
                        )
                    return source_row_id, is_abstract, source_column_id, column_label, column_position
        return None, is_abstract, source_column_id, None, column_position

    source_row_metadata = [get_source_row_metadata(label) for label in labels]
    for index, ((kind, value, text), (source_row_id, is_abstract, _, _, _)) in enumerate(
        zip(classified_data, source_row_metadata)
    ):
        if is_abstract and kind == "item":
            classified_data[index] = ("parent", {"level": 1, "label": turkish_lower(text)}, text)

    rows = []
    current_dates, number_buffer = [], []
    current_table, current_item = None, None
    current_section = None
    parent_stack = []
    current_line_item_path = None
    current_source_row_id = None
    prev_kind, rpt_unit_index = None, 0
    current_reporting_unit = reporting_unit[0] if reporting_unit else 1
    current_report_kind = report_kind[0] if report_kind else None

    for (kind, value, text), (
        source_row_id, _, source_column_id, column_label, column_position
    ) in zip(
        classified_data, source_row_metadata
    ):

        if kind == "date":
            if prev_kind != "date":
                current_dates = [value]
                number_buffer = []
                current_item = None
                current_table = None
                parent_stack = []
                # assign reporting unit correctly
                if rpt_unit_index < len(reporting_unit):
                    current_reporting_unit = reporting_unit[rpt_unit_index]
                    current_report_kind = report_kind[rpt_unit_index]
                rpt_unit_index += 1
            else:
                current_dates.append(value)

        elif kind == "tableName":
            current_table = value
            parent_stack = []
            if value == "Statement of Cash Flows":
                current_section = "cash flow statement"
            elif value == "Statement of Change in Equity":
                current_section = "equity"
            elif value in {"Balance Sheet", "Income Statement"}:
                current_section = None
            if text == "Diğer Kapsamlı Gelir Tablosu":
                current_section = "other comprehensive income"
        
        elif kind == "section":
            current_section = value
            parent_stack = []

        elif kind in {"item", "parent"}:
            current_item = turkish_lower(value if kind == "item" else text)
            current_source_row_id = source_row_id
            if kind == "parent":
                level = int(value["level"])
                parent_stack = parent_stack[: max(level - 1, 0)]
                parent_stack.append(value["label"])
                path_parts = [part for part in [current_section, *parent_stack] if part]
            else:
                path_parts = [part for part in [current_section, *parent_stack, current_item] if part]
            current_line_item_path = " > ".join(path_parts) if path_parts else current_item
            number_buffer = []

        elif kind == "number":
            if column_position is not None:
                if column_position < len(current_dates):
                    rows.append({
                    "company": company.symbol,
                    "year": reporting_year,
                    "quarter": reporting_period,
                    "date": current_dates[column_position],
                    "table": current_table,
                    "section": current_section,
                    "line_item_path": current_line_item_path,
                    "source_row_id": current_source_row_id,
                    "source_column_id": source_column_id if current_table == "Statement of Change in Equity" else None,
                    "column_label": column_label if current_table == "Statement of Change in Equity" else None,
                    "item": current_item,
                    "value": value,
                    "reporting unit": current_reporting_unit,
                    "report_kind": current_report_kind,
                    "file_path": file_path,
                    "source_filename": source_filename,
                    "filing_id": filing_id,
                })
            else:
                number_buffer.append(value)
                if len(number_buffer) == len(current_dates):
                    for date, num in zip(current_dates, number_buffer):
                        rows.append({
                            "company": company.symbol,
                            "year": reporting_year,
                            "quarter": reporting_period,
                            "date": date,
                            "table": current_table,
                            "section": current_section,
                            "line_item_path": current_line_item_path,
                            "source_row_id": current_source_row_id,
                            "source_column_id": source_column_id if current_table == "Statement of Change in Equity" else None,
                            "column_label": column_label if current_table == "Statement of Change in Equity" else None,
                            "item": current_item,
                            "value": num,
                            "reporting unit": current_reporting_unit,
                            "report_kind": current_report_kind,
                            "file_path": file_path,
                            "source_filename": source_filename,
                            "filing_id": filing_id,
                        })
                    number_buffer = []

        prev_kind = kind
    
    df = pd.DataFrame(rows)
    df = df.astype({"company": "str", "year": "int", "quarter": "int", "date": "str",
                     "table": "str", "section": "str", "line_item_path": "str", "source_row_id": "str",
                     "source_column_id": "str", "column_label": "str",
                     "item": "str", "value": "float", "reporting unit": "float",
                     "report_kind": "str", "file_path": "str", "source_filename": "str"})
    
    statement = FinancialStatement(company, reporting_year, reporting_period, df, file_path)
    
    return statement
