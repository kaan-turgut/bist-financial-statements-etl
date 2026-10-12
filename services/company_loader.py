import json

from models.company import Company
from paths import DATA_MAPPINGS_DIR


def load_companies(json_path=DATA_MAPPINGS_DIR / "company_info.json"):

    with open(json_path, encoding="utf-8") as f:

        company_info = json.load(f)

    companies = {}

    for symbol, data in company_info.items():

        companies[symbol] = Company.from_kap_data(symbol, data)

    return companies
