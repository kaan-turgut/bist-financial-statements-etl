import logging
import re
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


def statement_period_from_path(file_path):
    zip_path, inner_file = str(file_path).split(".zip/", 1)
    year_match = re.search(r"_(\d{4})$", Path(zip_path).stem)
    quarter_match = re.search(r"_(\d)$", Path(inner_file).stem)
    if not year_match or not quarter_match:
        raise ValueError(f"Cannot determine statement period from {file_path}")
    return int(year_match.group(1)), int(quarter_match.group(1))

def obtain_statement_paths(company_folder_path, begin_year, begin_quarter, end_year, end_quarter):
    company_folder_path = Path(company_folder_path)

    statement_paths = []
    if not company_folder_path.exists():
        logger.warning("Statement folder does not exist: %s", company_folder_path)
        return statement_paths

    # loop through items in the folder
    for zip_path in company_folder_path.glob("*.zip"):
        try:
            year = int(zip_path.stem.split("_")[1])
        except (IndexError, ValueError):
            logger.warning("Skipping ZIP with unexpected name: %s", zip_path)
            continue

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_name in zip_ref.namelist():
                if file_name.lower().endswith(".xls"):
                    try:
                        quarter = int(Path(file_name).stem.split("_")[-1])
                    except ValueError:
                        logger.warning("Skipping XLS with unexpected name: %s", file_name)
                        continue

                    if begin_year + begin_quarter/10 <= year + quarter/10 <= end_year + end_quarter/10:
                        statement_paths.append(str(zip_path / file_name))

    return statement_paths
