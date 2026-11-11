import sqlite3
from pathlib import Path
from contextlib import contextmanager

"""SQLite helpers for the vendor platform.

Usage:
    import db
    db.ensure_db()                                  # once at app start
    vendors = db.query("SELECT * FROM vendors WHERE status = ?", ("active",))
    vendor_id = db.execute("INSERT INTO vendors (name, normalized_name) VALUES (?, ?)", (n, nn))
"""

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "vendor_platform.db"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"

# Set date to the given date in README
TODAY = "2026-10-20"


def get_conn() -> sqlite3.Connection:
    """Open a short-lived connection with the settings every query needs."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON") # enable foreign keys
    return conn


@contextmanager
def transaction():
    """Commit on success, roll back on any exception, always close."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return all rows as a list of dicts."""
    with transaction() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    """Run a SELECT and return the first row as a dict, or None."""
    with transaction() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def scalar(sql: str, params: tuple = ()):
    """Return the first column of the first row, or None. Useful for COUNT statements."""
    with transaction() as conn:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None


def execute(sql: str, params: tuple = ()) -> int:
    """Run a single INSERT/UPDATE/DELETE. Returns lastrowid (useful for INSERT)."""
    with transaction() as conn:
        return conn.execute(sql, params).lastrowid


def execute_many(sql: str, rows: list[tuple]) -> None:
    """Bulk insert/update in one transaction (good for seeding)."""
    with transaction() as conn:
        conn.executemany(sql, rows)


def init_db(reset: bool = False) -> None:
    """Create all tables from schema.sql. reset=True deletes the DB file first."""
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    conn = get_conn()
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def is_initialized() -> bool:
    """True if the DB file exists and has at least one user table."""
    if not DB_PATH.exists():
        return False
    row = query_one(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return bool(row and row["n"] > 0)


def ensure_db() -> None:
    """Build the schema (and seed) if the DB is missing."""
    if is_initialized():
        return
    init_db()
    import seed
    seed.run()


if __name__ == "__main__":
    # `python db.py` rebuilds the database from scratch during development.
    init_db(reset=True)
    print(f"Database created at {DB_PATH}")