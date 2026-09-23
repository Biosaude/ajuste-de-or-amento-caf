import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
DB = DATA / "app.db"


def init_db() -> None:
    with sqlite3.connect(DB) as db:
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, spreadsheet_name TEXT NOT NULL,
          pdf_name TEXT NOT NULL, budget_number TEXT NOT NULL, item_count INTEGER NOT NULL,
          found_count INTEGER NOT NULL, missing_count INTEGER NOT NULL, integrity TEXT NOT NULL)""")


def set_base(metadata: dict) -> None:
    with sqlite3.connect(DB) as db:
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('base',?)", (json.dumps(metadata),))


def get_base() -> dict | None:
    with sqlite3.connect(DB) as db:
        row = db.execute("SELECT value FROM settings WHERE key='base'").fetchone()
    return json.loads(row[0]) if row else None


def audit(**values: object) -> None:
    with sqlite3.connect(DB) as db:
        db.execute("INSERT INTO audit(created_at,spreadsheet_name,pdf_name,budget_number,item_count,found_count,missing_count,integrity) VALUES(?,?,?,?,?,?,?,?)",
                   (datetime.now(timezone.utc).isoformat(), values["spreadsheet_name"], values["pdf_name"], values["budget_number"],
                    values["item_count"], values["found_count"], values["missing_count"], values["integrity"]))

