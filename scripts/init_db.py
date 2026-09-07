from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import DB_PATH, db


def initialize_database() -> None:
    with db() as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Database integrity check failed: {result}")

    print(f"Database initialized: {DB_PATH}")


if __name__ == "__main__":
    initialize_database()
