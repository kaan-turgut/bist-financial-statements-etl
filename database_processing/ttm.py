
def calculate_ttm(conn):

    cursor = conn.cursor()

    # Check existing columns in ratios
    cursor.execute("PRAGMA table_info(income_statement)")
    cols = [col[1] for col in cursor.fetchall()]

    if "line_item_path" in cols:
        if "section" in cols:
            line_key = "COALESCE(line_item_path, COALESCE(section, '') || ' > ' || item)"
        else:
            line_key = "COALESCE(line_item_path, item)"
        line_match = f"{line_key} = COALESCE(t.line_item_path, COALESCE(t.section, '') || ' > ' || t.item)" if "section" in cols else f"{line_key} = COALESCE(t.line_item_path, t.item)"
    elif "section" in cols:
        line_match = "section = t.section AND item = t.item"
    else:
        line_match = "item = t.item"

    # Add only if missing
    if "ttm_value" not in cols:
        cursor.execute("ALTER TABLE income_statement ADD COLUMN ttm_value REAL;")
    if "ttm_value_adj_inflation" not in cols:
        cursor.execute("ALTER TABLE income_statement ADD COLUMN ttm_value_adj_inflation REAL;")

    # Calculate ttm for value and adj_value_inflation on income statement
    cursor.execute(f"""
            UPDATE income_statement AS t
    SET
        ttm_value = (
            SELECT
                CASE
                    WHEN COUNT(*) = 4 THEN SUM(value)
                    ELSE NULL
                END
            FROM income_statement
            WHERE company = t.company
            AND {line_match}
            AND (year * 4 + quarter) BETWEEN (t.year * 4 + t.quarter - 3) AND (t.year * 4 + t.quarter)
        ),
        ttm_value_adj_inflation = (
            SELECT
                CASE
                    WHEN COUNT(*) = 4 THEN SUM(value_adj_inflation)
                    ELSE NULL
                END
            FROM income_statement
            WHERE company = t.company
            AND {line_match}
            AND (year * 4 + quarter) BETWEEN (t.year * 4 + t.quarter - 3) AND (t.year * 4 + t.quarter)
        );
            """)

    conn.commit()
