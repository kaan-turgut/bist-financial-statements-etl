import pandas as pd

from database_processing.helpers import backup_sqlite


def delete_exact_duplicates(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if not columns:
        return

    group_by = ", ".join(columns)
    cursor.execute(f"""
    DELETE FROM {table}
    WHERE rowid NOT IN (
        SELECT MIN(rowid)
        FROM {table}
        GROUP BY {group_by}
    )
    """)


def clean_duplicates(conn):

    cursor = conn.cursor()

    for table in ["balance_sheet", "income_statement", "cash_flow", "equity_change"]:
        delete_exact_duplicates(cursor, table)

    conn.commit()
    

def fix_cumulative_q4(df):
    # copy dataframe to avoid modifying original
    df = df.copy()

    # identify cumulative rows
    cum_rows = df["quarter"] == 1234

    line_keys = ["company", "year"]
    if "line_item_path" in df.columns:
        line_keys.append("line_item_path")
    elif "section" in df.columns:
        line_keys.extend(["section", "item"])
    else:
        line_keys.append("item")

    sum_q1_q3 = (
        df[df["quarter"].isin([1, 2, 3])]
        .groupby(line_keys, as_index=False)["value"]
        .sum()
        .rename(columns={"value": "sum_q1_q3"})
    )

    # merge sums into cumulative rows
    df = df.merge(sum_q1_q3, on=line_keys, how="left")

    # replace 1234 value with Q4 = cumulative - sum(Q1-Q3)
    df.loc[cum_rows, "value"] = df.loc[cum_rows, "value"] - df.loc[cum_rows, "sum_q1_q3"]
    df.loc[cum_rows, "quarter"] = 4

    # drop helper column
    df = df.drop(columns="sum_q1_q3")

    return df


#  Turn the cumulative fourth quarter values from income statements into quarterly values and COMMIT the change in the database
def decumulate(conn, *, create_backup=True):
    
    cursor = conn.cursor()

    data = cursor.execute("SELECT * FROM income_statement")
    columns = [desc[0] for desc in cursor.description]
    
    df_income_statements = pd.DataFrame(data.fetchall(), columns=columns).astype(
        {"year": "int", "quarter": "int"}
    )
    
    df_income_statements_noncum = fix_cumulative_q4(df_income_statements)

    if create_backup:
        backup_sqlite(connection=conn)

    df_income_statements_noncum.to_sql("income_statement", conn, if_exists="replace", index=False)

    conn.commit()
