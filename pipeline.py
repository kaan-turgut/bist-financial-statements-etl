import argparse
import logging

from database_processing.helpers import resolve_inflation_period, restore_sqlite_backup
from logging_config import configure_logging
from services.company_loader import load_companies
from services.download_statements import download
from services.exchange_fetcher import get_exchange
from services.inflation_fetcher import get_inflation
from services.price_fetcher import adjust_price_table_for_inflation, update_prices
from services.update_database import update_database

logger = logging.getLogger(__name__)


def add_company_arguments(parser):
    parser.add_argument(
        "--symbols",
        nargs="+",
        help="Company symbols to process. Defaults to all companies in company_info.json.",
    )


def add_period_arguments(parser, *, include_price_level=False):
    parser.add_argument("--begin-year", type=int, required=True)
    parser.add_argument("--begin-quarter", type=int, choices=[1, 2, 3, 4], required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--end-quarter", type=int, choices=[1, 2, 3, 4], required=True)
    if include_price_level:
        parser.add_argument(
            "--price-level-year", type=int,
            help="Inflation adjustment target year; defaults to the latest month in inflation_table.",
        )
        parser.add_argument(
            "--price-level-month", type=int, choices=range(1, 13),
            help="Inflation adjustment target month; defaults to the latest month in inflation_table.",
        )


def build_parser():
    parser = argparse.ArgumentParser(description="Download and process BIST financial statements.")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser(
        "run", help="Refresh reference data, download statements, and update the database."
    )
    add_company_arguments(run_parser)
    add_period_arguments(run_parser, include_price_level=True)
    run_parser.add_argument("--skip-download", action="store_true")
    run_parser.add_argument("--skip-reference-data", action="store_true")
    run_parser.add_argument("--request-sleep", type=float, default=5.0)
    run_parser.add_argument("--rate-limit-delay", type=float, default=120.0)

    download_parser = commands.add_parser("download", help="Download annual KAP statement archives only.")
    add_company_arguments(download_parser)
    download_parser.add_argument("--begin-year", type=int, required=True)
    download_parser.add_argument("--end-year", type=int, required=True)
    download_parser.add_argument("--request-sleep", type=float, default=5.0)
    download_parser.add_argument("--rate-limit-delay", type=float, default=120.0)

    commands.add_parser("update-reference-data", help="Refresh inflation and USD/TRY tables only.")

    restore_parser = commands.add_parser(
        "restore-backup", help="Restore a database snapshot after confirming the replacement."
    )
    restore_parser.add_argument("--backup-file", required=True, help="Path to a snapshot in database_backups/")
    restore_parser.add_argument("--confirm", action="store_true", help="Confirm replacing the active database.")

    database_parser = commands.add_parser(
        "update-database", help="Parse local archives and rebuild database values only."
    )
    add_company_arguments(database_parser)
    add_period_arguments(database_parser, include_price_level=True)

    prices_parser = commands.add_parser(
        "update-prices", help="Download historical prices and optionally adjust them for inflation."
    )
    add_company_arguments(prices_parser)
    prices_parser.add_argument("--begin-date", required=True, help="YYYY-MM-DD or DD-MM-YYYY")
    prices_parser.add_argument("--end-date", required=True, help="YYYY-MM-DD or DD-MM-YYYY")
    prices_parser.add_argument("--request-sleep", type=float, default=1.5)
    prices_parser.add_argument("--rate-limit-delay", type=float, default=120.0)
    prices_parser.add_argument("--price-level-year", type=int)
    prices_parser.add_argument("--price-level-month", type=int, choices=range(1, 13))

    adjust_prices_parser = commands.add_parser(
        "adjust-prices", help="Recalculate stored inflation-adjusted prices without downloading."
    )
    add_company_arguments(adjust_prices_parser)
    adjust_prices_parser.add_argument("--price-level-year", type=int, required=True)
    adjust_prices_parser.add_argument(
        "--price-level-month", type=int, choices=range(1, 13), required=True
    )
    return parser


def resolve_companies(symbols):
    companies_by_symbol = load_companies()
    if not symbols:
        return list(companies_by_symbol.values())

    normalized_symbols = [symbol.upper() for symbol in symbols]
    missing = sorted(set(normalized_symbols) - set(companies_by_symbol))
    if missing:
        raise ValueError(f"Unknown company symbols: {', '.join(missing)}")
    return [companies_by_symbol[symbol] for symbol in normalized_symbols]


def refresh_reference_data():
    logger.info("Updating monthly inflation data")
    get_inflation()
    logger.info("Updating monthly USD/TRY data")
    get_exchange()
    logger.info("Reference data update complete")


def download_statements(companies, begin_year, end_year, request_sleep, rate_limit_delay):
    logger.info("Downloading archives for %s companies, %s-%s", len(companies), begin_year, end_year)
    download(
        begin_year=begin_year,
        end_year=end_year,
        companies=companies,
        request_sleep=request_sleep,
        rate_limit_delay=rate_limit_delay,
    )


def process_database(companies, args):
    requested_price_level = None
    if args.price_level_year is not None and args.price_level_month is not None:
        requested_price_level = (args.price_level_year, args.price_level_month)
    price_level = resolve_inflation_period(requested_price_level)
    logger.info(
        "Updating database for %s companies, %s Q%s through %s Q%s; inflation price level %s-%02d",
        len(companies), args.begin_year, args.begin_quarter, args.end_year, args.end_quarter,
        *price_level,
    )
    update_database(
        companies=companies,
        begin_year=args.begin_year,
        begin_quarter=args.begin_quarter,
        end_year=args.end_year,
        end_quarter=args.end_quarter,
        price_level=price_level,
    )
    logger.info("Database update complete")


def main(argv=None):
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {"run", "update-database"}:
        begin_period = args.begin_year * 4 + args.begin_quarter
        end_period = args.end_year * 4 + args.end_quarter
        if begin_period > end_period:
            parser.error("the begin year/quarter must not be later than the end year/quarter")
        if (args.price_level_year is None) != (args.price_level_month is None):
            parser.error("provide both --price-level-year and --price-level-month, or neither")
    elif args.command == "download" and args.begin_year > args.end_year:
        parser.error("--begin-year must not be later than --end-year")

    if args.command == "update-reference-data":
        refresh_reference_data()
        return

    if args.command == "restore-backup":
        if not args.confirm:
            raise SystemExit("Restoring replaces the active database. Re-run with --confirm after closing database clients.")
        backup_path = restore_sqlite_backup(args.backup_file)
        logger.info("Database restored from %s", backup_path)
        return

    companies = resolve_companies(args.symbols)
    if args.command == "download":
        download_statements(
            companies, args.begin_year, args.end_year, args.request_sleep, args.rate_limit_delay
        )
    elif args.command == "update-database":
        process_database(companies, args)
    elif args.command == "run":
        if not args.skip_reference_data:
            refresh_reference_data()
        if not args.skip_download:
            download_statements(
                companies, args.begin_year, args.end_year, args.request_sleep, args.rate_limit_delay
            )
        process_database(companies, args)
    elif args.command == "update-prices":
        if (args.price_level_year is None) != (args.price_level_month is None):
            parser.error("provide both --price-level-year and --price-level-month, or neither")
        symbols = [company.symbol for company in companies]
        logger.info("Downloading historic prices for %s companies", len(symbols))
        update_prices(
            symbols=symbols,
            begin_date=args.begin_date,
            end_date=args.end_date,
            sleep_seconds=args.request_sleep,
            rate_limit_delay=args.rate_limit_delay,
        )
        if args.price_level_year is not None:
            adjust_price_table_for_inflation(
                args.price_level_year, args.price_level_month, symbols=symbols
            )
    elif args.command == "adjust-prices":
        symbols = [company.symbol for company in companies] if args.symbols else None
        adjust_price_table_for_inflation(
            args.price_level_year, args.price_level_month, symbols=symbols
        )


if __name__ == "__main__":
    main()
