from catalog.db import connect


def test_connect_enables_wal_and_safe_pragmas(tmp_path):
    conn = connect(tmp_path / "catalog.sqlite")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        # synchronous=NORMAL is 1; foreign_keys must stay ON for cascade integrity.
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_connect_in_memory_skips_wal(tmp_path):
    # In-memory databases do not support WAL; connect() must not raise.
    conn = connect(":memory:")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "memory"
    finally:
        conn.close()
