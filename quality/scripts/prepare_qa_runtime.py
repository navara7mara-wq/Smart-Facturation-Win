"""Prepare the isolated RC-1 runtime; never point this at the production DB."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    database = Path(os.environ["PHOENIX_DB_PATH"]).resolve()
    allowed_root = (PROJECT_ROOT / "tmp" / "rc1-audit").resolve()
    if allowed_root not in database.parents:
        raise RuntimeError(f"Refusing non-QA database: {database}")
    from db import db
    from services.auth import hash_password

    with db() as connection:
        connection.execute("DELETE FROM user_sessions")
        connection.execute(
            """
            UPDATE users
            SET password_hash=?, role='super_admin', company_branch_id=NULL,
                is_active=1, must_change_password=0, failed_attempts=0,
                locked_until=NULL
            WHERE username='admin'
            """,
            (hash_password("QA_RC1_Pass123"),),
        )
    print(f"Prepared isolated QA runtime: {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
