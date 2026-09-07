# Automated Quality Gates — RC-1

Audit host: Windows 11, Python 3.14.6, Node/Playwright bundled or project dependencies.

| Command | Result |
|---|---|
| `python -m pytest -q -W error` | PASS — 79 passed in 331.68s |
| `python -m pytest -m unit -q -W error` | PASS — 20 passed, 59 deselected, 0.41s |
| `python -m pytest -m integration -q -W error` | PASS — 18 passed, 61 deselected, 183.31s |
| `python -m pytest -m e2e -q -W error` | PASS — 2 passed, 77 deselected, 44.77s |
| `python -m pytest -m migration -q -W error` | PASS — 5 passed, 74 deselected, 44.33s |
| unmarked tests | PASS — 34 passed, 45 deselected, 26.10s |
| `powershell ... scripts/run_visual_tests.ps1 -Strict` | PASS — 16/16 pages; minimum similarity 99.802%; strict console/overflow checks passed |
| `python -m compileall ...` | PASS |
| `python -m pip check` | PASS — No broken requirements found |
| `npm audit --omit=dev --json` | PASS — 0 vulnerabilities across 4 production dependencies |
| `python -m pytest --collect-only -q` | 79 tests collected |

Static/mutation tools `ruff`, `mypy`, `bandit`, `pip-audit`, and `mutmut` were not installed. Their absence is reported as a limitation and not converted to PASS. Manual targeted security review was performed.

Existing automated suites passing does not override the new RC audit failures in financial oracles, document-rate labelling, destructive GET, default credentials, or client address edit.
