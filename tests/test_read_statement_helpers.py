import pandas as pd

from read_statements.helpers import classify_texts, parse_date_year, parse_interval, turkish_lower


def test_parse_interval_for_range_date():
    assert parse_interval("01.01.2024 - 31.03.2024") == 90


def test_parse_interval_for_single_date():
    assert parse_interval("31.03.2024") is None


def test_parse_date_year_for_range_uses_start_year():
    assert parse_date_year("01.01.2024 - 31.03.2024") == 2024


def test_turkish_lower_handles_dotted_and_dotless_i():
    assert turkish_lower("IİŞÇÖÜĞ") == "ıişçöüğ"


def test_classify_texts_uses_statement_mappings():
    classified = classify_texts(["Bilanço", "31.03.2024", "1.234", "Hasılat"])
    assert classified[0][0] == "tableName"
    assert classified[1] == ("date", "31.03.2024", "31.03.2024")
    assert classified[2] == ("number", 1234, "1.234")
    assert classified[3] == ("item", "Hasılat", "Hasılat")


def test_pandas_is_available_for_pipeline_tests():
    frame = pd.DataFrame({"value": [1, 2, 3]})
    assert frame["value"].sum() == 6

