import sqlite3

import pytest

from database_processing.helpers import backup_sqlite, restore_sqlite_backup


def create_database(path, value):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample (value INTEGER)")
        conn.execute("INSERT INTO sample VALUES (?)", (value,))


def test_backup_creates_distinct_verified_snapshots(tmp_path):
    database = tmp_path / "bist_data.db"
    backup_dir = tmp_path / "database_backups"
    create_database(database, 1)

    first = backup_sqlite(database, backup_dir)
    second = backup_sqlite(database, backup_dir)

    assert first.exists()
    assert second.exists()
    assert first != second
    with sqlite3.connect(first) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == 1


def test_restore_saves_current_database_before_replacing_it(tmp_path):
    database = tmp_path / "bist_data.db"
    backup_dir = tmp_path / "database_backups"
    create_database(database, 1)
    snapshot = backup_sqlite(database, backup_dir)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE sample SET value = 2")

    restored = restore_sqlite_backup(snapshot, database, backup_dir)

    assert restored == snapshot.resolve()
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == 1
    recovery_snapshots = list(backup_dir.glob("bist_data_*.db"))
    assert len(recovery_snapshots) == 2
    assert any(path != snapshot for path in recovery_snapshots)


def test_backup_skips_missing_or_in_memory_databases(tmp_path):
    assert backup_sqlite(tmp_path / "missing.db", tmp_path / "backups") is None
    conn = sqlite3.connect(":memory:")
    try:
        assert backup_sqlite(connection=conn, backup_dir=tmp_path / "backups") is None
    finally:
        conn.close()


def test_restore_rejects_unhealthy_or_active_database(tmp_path):
    database = tmp_path / "bist_data.db"
    create_database(database, 1)
    with pytest.raises(ValueError, match="active database"):
        restore_sqlite_backup(database, database)

    invalid_backup = tmp_path / "invalid.db"
    invalid_backup.write_text("not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        restore_sqlite_backup(invalid_backup, database)
