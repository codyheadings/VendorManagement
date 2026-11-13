import sqlite3
from pathlib import Path
from contextlib import contextmanager
import os
import re

"""SQLite helpers for the vendor platform.
"""

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "vendor_platform.db"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"

# Set date to the given date in README
TODAY = "2026-10-20"

# disable mail & slack integrations for deployment
SEND_ENABLED = False
NOTIFICATIONS_ENABLED = False

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


def schema_tables() -> list[str]:
    """Table names declared in schema.sql, so the check below never goes out of date."""
    return re.findall(r"^\s*CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)", SCHEMA_PATH.read_text(encoding="utf-8"),
                      re.I | re.M)
 
 
def missing_tables() -> list[str]:
    """Tables schema.sql defines that the database does not have (all of them if there is no file)."""
    wanted = schema_tables()
    if not DB_PATH.exists():
        return wanted
    conn = sqlite3.connect(DB_PATH)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    return [t for t in wanted if t not in have]
 
 
def find_bad_statement(sql: str) -> str:
    """Run schema.sql one statement at a time and name the first one SQLite rejects (with its line number)."""
    conn = sqlite3.connect(":memory:")
    buf, start = "", 1
    for n, line in enumerate(sql.splitlines(), 1):
        if not buf.strip():
            start = n
        buf += line + "\n"
        if sqlite3.complete_statement(buf):
            try:
                conn.execute(buf)
            except sqlite3.Error as e:
                head = " ".join(buf.split())[:80]
                return f"line {start}-{n}: {e} -> {head}"
            buf = ""
    return "could not isolate the statement"
 
 
def init_db(reset: bool = False) -> None:
    """Build the whole schema in a temporary file, then swap it in.
 
    A failure part-way leaves the old file alone instead of a database with only the first
    few tables. A replaced file is kept next to it as <name>.bak.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    building = DB_PATH.with_name(DB_PATH.name + ".building")
    building.unlink(missing_ok=True)
    conn = sqlite3.connect(building)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    except Exception as e:
        conn.close()
        building.unlink(missing_ok=True)
        raise RuntimeError(f"schema.sql failed on SQLite {sqlite3.sqlite_version}: {e}. "
                           f"First bad statement: {find_bad_statement(SCHEMA_PATH.read_text(encoding='utf-8'))}") from e
    conn.close()
    if DB_PATH.exists():
        os.replace(DB_PATH, DB_PATH.with_name(DB_PATH.name + ".bak"))
    os.replace(building, DB_PATH)
 
 
def is_initialized() -> bool:
    """True only if every table in schema.sql exists (one table is not enough)."""
    return not missing_tables()
 
 
def ensure_db() -> None:
    """Build the schema if the DB is missing."""
    if is_initialized():
        return
    init_db()


if __name__ == "__main__":
    # `python db.py` rebuilds the database from scratch during development.
    init_db(reset=True)
    print(f"Database created at {DB_PATH}")