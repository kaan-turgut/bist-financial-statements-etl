import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from paths import DATABASE_BACKUP_DIR, DATABASE_PATH

logger = logging.getLogger(__name__)


def resolve_inflation_period(price_level=None, db_path=DATABASE_PATH):
    """Resolve a requested price level or use the latest month in inflation_table."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found at {db_path}; populate reference data first.")

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inflation_table'"
        ).fetchone()
        if not table_exists:
            raise RuntimeError("inflation_table is missing; run update-reference-data first.")

        if price_level is None:
            row = conn.execute(
                "SELECT year, month FROM inflation_table ORDER BY year DESC, month DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("inflation_table is empty; run update-reference-data first.")
            return int(row[0]), int(row[1])

        if len(price_level) != 2:
            raise ValueError("price_level must be a (year, month) pair")
        year, month = map(int, price_level)
        if not 1 <= month <= 12:
            raise ValueError("price-level month must be between 1 and 12")
        exists = conn.execute(
            "SELECT 1 FROM inflation_table WHERE year=? AND month=? LIMIT 1", (year, month)
        ).fetchone()
        if not exists:
            raise ValueError(f"No inflation data is available for {year}-{month:02d}.")
        return year, month


def _database_path_from_connection(conn):
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


def backup_sqlite(db_path=DATABASE_PATH, backup_dir=DATABASE_BACKUP_DIR, *, connection=None):
    """Create an integrity-checked, timestamped SQLite snapshot; return None for in-memory DBs."""
    db_path = Path(db_path) if db_path is not None else None
    if connection is None and (db_path is None or not db_path.is_file()):
        logger.info("Skipping database backup because the source database does not exist")
        return None

    if connection is not None:
        db_path = _database_path_from_connection(connection)
        if db_path is None:
            logger.info("Skipping database backup for an in-memory SQLite database")
            return None

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"{db_path.stem}_{timestamp}{db_path.suffix}"
    destination = None
    source = connection
    owns_source = source is None

    try:
        if owns_source:
            source = sqlite3.connect(db_path, timeout=30)
        destination = sqlite3.connect(backup_path, timeout=30)
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"Backup integrity check failed: {integrity}")
    except Exception:
        if destination is not None:
            destination.close()
            destination = None
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        if destination is not None:
            destination.close()
        if owns_source and source is not None:
            source.close()

    logger.info("Database backup created and verified: %s", backup_path)
    return backup_path


def restore_sqlite_backup(backup_path, db_path=DATABASE_PATH, backup_dir=DATABASE_BACKUP_DIR):
    """Restore a verified snapshot, preserving the current database as a pre-restore backup."""
    backup_path = Path(backup_path).expanduser().resolve()
    db_path = Path(db_path).expanduser().resolve()
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    if backup_path == db_path:
        raise ValueError("The selected backup is the active database")

    with closing(sqlite3.connect(backup_path, timeout=30)) as source:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"Selected backup is not healthy: {integrity}")

    if db_path.is_file():
        backup_sqlite(db_path, backup_dir)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(backup_path, timeout=30)) as source,
        closing(sqlite3.connect(db_path, timeout=30)) as destination,
    ):
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"Restored database integrity check failed: {integrity}")

    logger.info("Restored database from %s", backup_path)
    return backup_path
