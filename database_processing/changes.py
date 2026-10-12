def _line_key_expression(columns, alias=None):
    prefix = f"{alias}." if alias else ""
    item = f"{prefix}item"
    if "line_item_path" in columns and "section" in columns:
        return f"COALESCE({prefix}line_item_path, COALESCE({prefix}section, '') || ' > ' || {item})"
    if "line_item_path" in columns:
        return f"COALESCE({prefix}line_item_path, {item})"
    if "section" in columns:
        return f"COALESCE({prefix}section, '') || ' > ' || {item}"
    return item


def get_changes(conn):
    cursor = conn.cursor()
    tables = ["income_statement", "balance_sheet", "cash_flow"]

    for table in tables:
        columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
        if "yoy_pct_chg_real" not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN yoy_pct_chg_real REAL")
        if "qoq_pct_chg_real" not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN qoq_pct_chg_real REAL")
        if table == "income_statement" and "qoq_ttm_pct_chg_real" not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN qoq_ttm_pct_chg_real REAL")

    for table in tables:
        columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
        line_key = _line_key_expression(columns)

        for change_column, partition, ordering in [
            ("yoy_pct_chg_real", "company, line_key, quarter", "year"),
            ("qoq_pct_chg_real", "company, line_key", "year, quarter"),
        ]:
            cursor.execute(f"""
                WITH base AS (
                    SELECT rowid AS row_id, company, year, quarter,
                           {line_key} AS line_key, value_adj_inflation
                    FROM {table}
                ),
                calc AS (
                    SELECT row_id,
                           value_adj_inflation * 1.0 /
                           LAG(value_adj_inflation) OVER (
                               PARTITION BY {partition}
                               ORDER BY {ordering}
                           ) - 1 AS change_value
                    FROM base
                )
                UPDATE {table}
                SET {change_column} = (
                    SELECT calc.change_value FROM calc WHERE calc.row_id = {table}.rowid
                )
            """)

        if table == "income_statement":
            line_key_t = _line_key_expression(columns, "t")
            line_key_prev = _line_key_expression(columns, "prev")
            cursor.execute(f"""
                UPDATE {table} AS t
                SET qoq_ttm_pct_chg_real = (
                    SELECT CASE
                        WHEN prev.ttm_value_adj_inflation IS NOT NULL
                         AND prev.ttm_value_adj_inflation != 0
                        THEN t.ttm_value_adj_inflation * 1.0 /
                             prev.ttm_value_adj_inflation - 1
                        ELSE NULL
                    END
                    FROM {table} AS prev
                    WHERE prev.company = t.company
                      AND {line_key_prev} = {line_key_t}
                      AND (prev.year * 4 + prev.quarter) =
                          (t.year * 4 + t.quarter - 1)
                )
                WHERE t.ttm_value_adj_inflation IS NOT NULL
            """)

    conn.commit()
