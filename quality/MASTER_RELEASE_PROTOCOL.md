# PhoEniX BPU - Master Release Protocol

## Release candidate identity

- Audit label: **RC-1**
- Product-reported version: **2.5.1**
- Baseline commit: `965f0f81a368d3557a187f57424c87bff0c3298a`
- Branch: `master`
- Audit date: 2026-08-25 (Africa/Algiers)
- Audited state: the complete working tree present at audit start, including tracked modifications and untracked files. The baseline commit alone does not identify RC-1.
- Original business database: `data/pos_ai.sqlite3`; destructive business tests must use an isolated `PHOENIX_DB_PATH`. Closing verification must compare its complete logical contents with the protected baseline/backup, not rely on file bytes alone because SQLite checkpointing can change the physical SHA-256.

The exact hashes and repository state are recorded in `quality/evidence/RC1_BASELINE.md`. Closing protection evidence is in `quality/evidence/RC_PROTECTION_VERIFICATION.md`: the physical database hash changed, but the full schema, every row/value and all sequence values remained logically identical.

## Audit-only rules

1. Do not change application or production source behavior.
2. Do not run destructive tests against the original database, uploads, exports, backups, or user profile data.
3. Prefix QA records with `QA_RC1_` and use isolated paths under `tmp/rc1-audit/`.
4. Preserve logs, screenshots, database checks and output hashes as evidence.
5. A PASS requires evidence. Unexecuted or unobservable behavior is NOT TESTED.
6. Establish independent expected results before comparing financial outputs.
7. Stop after reporting blockers; remediation requires explicit authorization.

## Architecture discovered

- Desktop shell: Python `pywebview` in `desktop.py`.
- Local HTTP application: Python `BaseHTTPRequestHandler` / `ThreadingHTTPServer` in `app.py`, bound by desktop mode to loopback and a random local port.
- Frontend: server-generated HTML plus vanilla JavaScript and CSS; one separate HTML template for the new billing table.
- Database: SQLite through `db.py`; schema baseline in `database/schema.sql` and versioned migrations in `database/migrations.py`.
- Documents: XLSX generation through project services/OpenPyXL/template workbooks; PDF rendering through bundled Node.js/Playwright scripts.
- Packaging: PyInstaller desktop build, portable ZIP, Inno Setup installer, SHA-256 manifest.
- Authentication: server-side sessions in SQLite, password hashing, roles and granular permissions, CSRF tokens.
- Licensing: signed Ed25519 licence documents, demo limits and a separate publisher tool.

## Required command protocol

Development:

```powershell
python -m pip install -r requirements.txt
npm install
python app.py
python desktop.py
```

Mandatory verification:

```powershell
python -m pytest -q -W error
python -m py_compile app.py db.py desktop.py license_studio.py services/*.py scripts/*.py database/*.py
powershell -ExecutionPolicy Bypass -File scripts/run_visual_tests.ps1 -Strict
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1 -Version 2.5.1
powershell -ExecutionPolicy Bypass -File scripts/test_installer.ps1 -Version 2.5.1
```

The build and installer commands may overwrite `dist/`; audit execution must preserve the pre-audit artifact inventory first. This is the proposed stable QA content for a future `AGENTS.md`; no `AGENTS.md` existed at audit start, and none is created during RC-1 audit.

## Test sequence

1. Freeze identity and read-only integrity baseline.
2. Complete feature inventory and risk classification.
3. Define test data and independent financial oracles.
4. Run static checks, automated suites and build checks.
5. Run the actual application using isolated storage.
6. Execute critical/high-risk workflows and repetitions.
7. Inspect persistence, generated documents and database integrity.
8. Measure performance/resource baselines.
9. Perform security review and release-gate evaluation.
10. Issue Arabic audit report and preliminary verdict.
