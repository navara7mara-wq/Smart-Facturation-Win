from pathlib import Path
import sqlite3


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_DIR = ROOT_DIR / "data"
DB_PATH = DB_DIR / "pos_ai.sqlite3"
SCHEMA_PATH = ROOT_DIR / "database" / "schema.sql"


def initialize_database() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.executescript(schema)
        connection.commit()

    print(f"Database initialized: {DB_PATH}")


if __name__ == "__main__":
    initialize_database()
