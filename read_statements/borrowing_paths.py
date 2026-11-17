"""Recover borrowing counterparty branches omitted by older hierarchy mappings.

Bronze retains each source row and its order, including the related/unrelated
parent totals. Reconstruct only the two affected leaf paths; never combine
amounts or use a taxonomy row number as a cross-report financial identity.
"""

from unidecode import unidecode

from read_statements.helpers import load_statement_mappings

ROOTS = {
    "kisa vadeli borclanmalar": "diger kisa vadeli borclanmalar",
    "uzun vadeli borclanmalarin kisa vadeli kisimlari":
        "diger uzun vadeli borclanmalarin kisa vadeli kisimlari",
}
PARENTS = {
    f"{party} {root}": root
    for root in ROOTS
    for party in ("iliskili taraflardan", "iliskili olmayan taraflardan")
}
BORROWING_PATH_ITEMS = tuple([*ROOTS, *ROOTS.values(), *PARENTS])


def borrowing_path_overrides(source_rows):
    """Return corrected paths keyed by filing/row, from ordered Bronze rows.

    Input must be ordered by filing_key, source_row_number. Repeated date
    columns are harmless; a new borrowing total clears its previous branch.
    Rows without an explicit source parent remain unchanged.
    """
    _, _, hierarchy = load_statement_mappings()
    leaves = {leaf: root for root, leaf in ROOTS.items()}
    branches, overrides = {}, {}
    for row in source_rows:
        item = unidecode(row["item"]).lower()
        filing = row["filing_key"]
        path = row["line_item_path"] or ""
        if item in ROOTS:
            branches.pop((filing, item), None)
        elif item in PARENTS:
            root = PARENTS[item]
            root_path, separator, label = path.rpartition(" > ")
            if separator and label == hierarchy[item]["label"]:
                branches[(filing, root)] = (root_path, path)
        elif item in leaves:
            branch = branches.get((filing, leaves[item]))
            if branch and row["source_row_id"]:
                root_path, parent_path = branch
                leaf = hierarchy[item]["label"]
                # Already-correct paths and unfamiliar layouts are untouched.
                if path == f"{root_path} > {leaf}":
                    overrides[(filing, row["source_row_id"])] = f"{parent_path} > {leaf}"
    return overrides
