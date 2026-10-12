import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from paths import FINANCIAL_STATEMENTS_DIR

logger = logging.getLogger(__name__)


def download_file(company, year, retries=5, delay=5, request_sleep=5.0, rate_limit_delay=120):
    base_url = "https://www.kap.org.tr/tr/api/home-financial/download-file"

    symbol = company.symbol
    oid = company.oid

    url = f"{base_url}/{oid}/{year}/T"
    company_folder = FINANCIAL_STATEMENTS_DIR / symbol
    company_folder.mkdir(parents=True, exist_ok=True)
    filename = company_folder / f"{symbol}_{year}.zip"

    attempt = 0
    while attempt < retries:
        try:
            response = requests.get(url, stream=True, timeout=20)
            if request_sleep:
                time.sleep(request_sleep)

            if response.status_code == 429 and attempt < retries - 1:
                attempt += 1
                wait_seconds = rate_limit_delay * attempt
                logger.warning(
                    "KAP rate-limited %s %s; retrying in %.1fs (%s/%s)",
                    symbol,
                    year,
                    wait_seconds,
                    attempt,
                    retries,
                )
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            if len(response.content) < 200:
                logger.info("%s does not have a file for %s", symbol, year)
                return
            with open(filename, "wb") as f:
                f.writelines(response.iter_content(chunk_size=8192))
            logger.info("Downloaded %s", filename)
            return  # success, stop retrying
        except requests.exceptions.RequestException as e:
            attempt += 1
            if attempt < retries:
                logger.warning("Retry %s/%s for %s %s after error: %s", attempt, retries, symbol, year, e)
                time.sleep(delay)
            else:
                logger.error("Failed to download %s %s after %s attempts: %s", symbol, year, retries, e)


def download(begin_year, end_year, companies, max_workers=1, request_sleep=5.0, rate_limit_delay=120):
    tasks = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for company in companies:
            for year in range(begin_year, end_year + 1):
                tasks.append(executor.submit(
                    download_file,
                    company,
                    year,
                    request_sleep=request_sleep,
                    rate_limit_delay=rate_limit_delay,
                ))

        for future in as_completed(tasks):
            try:
                future.result()
            except Exception:
                logger.exception("Download task failed")
