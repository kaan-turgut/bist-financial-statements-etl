import zipfile
from types import SimpleNamespace

from read_statements import parse_statement as parser_module


def test_parse_statement_keeps_values_aligned_when_a_date_column_is_blank(
    tmp_path, monkeypatch
):
    html = """
    <table class="financial-table">
      <tr>
        <td colspan="3"></td>
        <td class="context-header" column-id="5"><div class="gwt-HTML multi-language-content content-tr">31.03.2018</div></td>
        <td class="context-header" column-id="6"><div class="gwt-HTML multi-language-content content-tr">31.12.2017</div></td>
        <td class="context-header" column-id="7"><div class="gwt-HTML multi-language-content content-tr">31.12.2016</div></td>
      </tr>
      <tr><td><div class="gwt-Label">Balance Sheet</div></td></tr>
      <tr class="general_role_210000-row-4 data-input-row">
        <td class="taxonomy-nondimensional-context-cell"></td>
        <td class="taxonomy-field-title"><div class="gwt-Label">Cash</div></td>
        <td class="taxonomy-footnote-cell"></td>
        <td class="taxonomy-context-value col-order-class-4"><div class="gwt-Label">480.402</div></td>
        <td class="taxonomy-context-value col-order-class-5"><div class="gwt-Label">1.262.904</div></td>
        <td class="taxonomy-context-value col-order-class-6"><div class="gwt-Label"></div></td>
      </tr>
    </table>
    """
    archive = tmp_path / "report.zip"
    member = "ABC_123_2018_1.xls"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(member, html)

    def classify(texts):
        classified = []
        for text in texts:
            if text == "Balance Sheet":
                classified.append(("tableName", text, text))
            elif text in {"31.03.2018", "31.12.2017", "31.12.2016"}:
                classified.append(("date", text, text))
            elif text == "Cash":
                classified.append(("item", text, text))
            else:
                classified.append(("number", float(text.replace(".", "")), text))
        return classified

    monkeypatch.setattr(parser_module, "parse_reporting_info", lambda soup: ("2018", "1", [1], ["Konsolide"]))
    monkeypatch.setattr(parser_module, "classify_texts", classify)

    statement = parser_module.parse_statement(
        f"{archive}/{member}", SimpleNamespace(symbol="ABC")
    )
    balance = statement.df[statement.df["table"] == "Balance Sheet"]

    assert balance[["date", "value"]].to_dict("records") == [
        {"date": "31.03.2018", "value": 480402.0},
        {"date": "31.12.2017", "value": 1262904.0},
    ]
