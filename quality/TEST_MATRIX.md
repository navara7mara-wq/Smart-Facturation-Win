# مصفوفة اختبارات RC-1 القابلة للتتبع

| Test Case ID | المتطلب / الميزة | Risk | Preconditions / Test Data | Expected Result | Actual Result | Evidence | Status | Defect |
|---|---|---|---|---|---|---|---|---|
| RC-001 | تعريف حالة المستودع | CRITICAL | Git + working tree | commit/branch/dirty/hashes | سُجل commit مع 45 ملفًا tracked معدلاً وuntracked؛ hashes ثابتة | `evidence/RC1_BASELINE.md` | PASS | |
| DB-001 | سلامة وحماية DB الأصلية | CRITICAL | `data/pos_ai.sqlite3` + نسخة QA سابقة للإغلاق | integrity ok وصفر FK/orphans ولا تغير منطقي | كل checks صفر؛ 17 migrations؛ تطابق كامل للمخطط وكل صف/قيمة/sequence رغم تغير hash الفيزيائي | `evidence/db_original.json`, `evidence/db_original_final.json`, `evidence/RC_PROTECTION_VERIFICATION.md` | PASS | |
| FIN-001 | 25 Golden calculations | CRITICAL | Decimal oracle | تطابق 25/25 | 22 PASS، 3 FAIL | `evidence/financial_oracles.json` | FAIL | DEF-RC1-002 |
| FIN-002 | Property financial invariants | CRITICAL | 2,000 generated combinations | لا discrepancy | 1,527 PASS و473 FAIL؛ تفاصيل أول 20 محفوظة | `evidence/financial_oracles.json` | FAIL | DEF-RC1-002 |
| INV-001 | 25 invoice create/read/edit/reopen | CRITICAL | varied dates, 1–5 lines, decimals | persistence + oracle exact | 22 PASS، 3 calculation FAIL؛ CRUD/persistence تم | `evidence/critical_invoice_scenarios.json` | FAIL | DEF-RC1-002 |
| INV-002 | Lifecycle valid/invalid transitions | CRITICAL | automated fixtures | valid allowed/invalid atomic reject | PASS | `pytest 79/79`; lifecycle tests | PASS | |
| INV-003 | Issued lock/unlock/authorization | CRITICAL | roles/reasons | DB/service lock and audit | PASS | lifecycle/workflow tests | PASS | |
| INV-004 | Cancel/restore | CRITICAL | cancelled QA fixture | authorized round-trip and lock | PASS | lifecycle tests | PASS | |
| INV-005 | Repeated Save / duplicate submit | CRITICAL | same invoice twice | one invoice/consistent lines | database_count=1 | `evidence/http_repetition_continuation.json` | PASS | |
| DOC-001 | XLSX content accuracy | CRITICAL | Normal/NDC + 10/20 rate | values and labels match DB/settings | RC-1 PRE-FIX: configurable labels FAIL | `remediation/evidence/pre_fix/DEF-RC1-003/rc1_audit/PRE_FIX_FAIL_rate_label_probe.json`; `PRE_FIX_FAIL_rate_10_20.xlsx` | FAIL (historical PRE-FIX) | DEF-RC1-003 |
| DOC-002 | PDF/DQ/DE visual and numeric accuracy | CRITICAL | 6 rendered PDFs + rate probe | no clipping; values/identity/rates correct | RC-1 PRE-FIX: configurable labels FAIL | `evidence/pdf_inspection.json`, `evidence/rendered/`, `remediation/evidence/pre_fix/DEF-RC1-003/rc1_audit/` | FAIL (historical PRE-FIX) | DEF-RC1-003 |
| PERS-001 | Restart persistence | CRITICAL | 10 QA invoices | active/deleted states and totals survive | 10/10 behavior correct; integrity ok | `evidence/persistence_restart.json` | PASS | |
| BAK-001 | Backup/download/restore | CRITICAL | known client address mutation | restored value/integrity/FK | value restored; integrity ok; 0 FK | `evidence/backup_restore_runtime.json` | PASS | |
| MIG-001 | Schema migrations/rollback | CRITICAL | legacy fixtures | preserve relations/financial values | 5 migration tests PASS | full pytest suite | PASS | |
| MIG-002 | Installer upgrade 2.5.0→2.5.1 | CRITICAL | real old/new installers + client marker | marker and DB preserved | marker preserved; integrity ok; uninstall retained DB | `build/upgrade-smoke/upgrade-smoke.json` | PASS | |
| SEC-001 | Fresh install authentication | CRITICAL | empty user-data | forced unique admin secret | default `admin/admin123`, no forced change | installer smoke | FAIL | DEF-RC1-001 |
| SEC-002 | Auth/session/permission enforcement | CRITICAL | unit/E2E roles | lockout/session/viewer restrictions | existing tests PASS | auth/e2e/lifecycle tests | PASS | |
| SEC-003 | CSRF and safe HTTP methods | HIGH | authenticated QA record | no state change by GET/no CSRF | POST rejected؛ GET delete changed state | `evidence/http_repetition_continuation.json` | FAIL | DEF-RC1-004 |
| SEC-004 | Injection/path/upload/headers | HIGH | code + runtime headers | contained/bounded/hardened | SQL/path acceptable؛ upload bounds/headers FAIL | `evidence/runtime_probe.json`; checklist | FAIL | DEF-RC1-006/007 |
| CLI-001 | Client create/edit/reopen | HIGH | 10 varied clients incl Arabic/accents | 10 create + 10 edit persist | create 10 PASS؛ address edit 0/10 | `evidence/high_risk_crud_edits.json` | FAIL | DEF-RC1-005 |
| CLI-002 | Client directions edit | HIGH | 10 directions | 10/10 persist/reopen | 10/10 PASS | `evidence/high_risk_crud_edits.json` | PASS | |
| BC-001 | Purchase-order create/edit/reopen | HIGH | 10 original + extra volume | 10/10 edit/reopen and relations | 10/10 PASS | `evidence/high_risk_crud_edits.json` | PASS | |
| SITE-001 | Site create/edit/reopen | HIGH | 10 original + extra volume | 10/10 persist and partner rules | 10/10 PASS | `evidence/high_risk_crud_edits.json` | PASS | |
| BPU-001 | BPU/ST mapping/import/version | CRITICAL | 610 general, 1,132 ST | canonical mapping and atomic import | automated mapping/import tests PASS | pytest suite | PASS | |
| IMP-001 | Partner/PO import/export | HIGH | valid/invalid workbooks | preview non-mutating, confirm/report | automated round-trip/reject tests PASS | pytest suite | PASS | |
| TAB-001 | Billing search/filter/sort/page/export | HIGH | canonical page + QA invoices | 10 searches found and visual stable | 10/10 searches PASS؛ strict visual PASS | `evidence/canonical_invoice_searches.json`, visual report | PASS | |
| DASH-001 | Dashboard KPIs/filters/volume | HIGH | lifecycle fixtures + 2,000 invoices | independent reconciliation | 5 dashboard + large dataset tests PASS | pytest suite | PASS | |
| REF-001 | Typology/partners/branches | HIGH | fixtures/imports | validation/normalization/relations | tests PASS؛ manual exhaustive delete NOT TESTED | pytest suite | PASS | |
| LIC-001 | Licence/signature/machine/limits | HIGH | valid/tampered/wrong-machine/demo | enforce signature/device/limits | 11 licence tests PASS | pytest suite | PASS | |
| VOL-001 | Volume/stability | HIGH | 610 BPU, 1,132 ST, 60 PO/sites, 49 runtime invoices + 2,000 test invoices | usable/no corruption | UI timings stable; integrity ok | perf + DB evidence | PASS | |
| PERF-001 | Measured baseline | MEDIUM | stable audit host | timings/resource values captured | captured; no short-window growth | `PERFORMANCE_BASELINE.md` | PASS | |
| BUILD-001 | Production build | CRITICAL | v2.5.1 tools | desktop/ZIP/installer/hashes | all built; hashes match | build output + `evidence/manifest_verification.json` | PASS | |
| INST-001 | Clean install/update/uninstall | CRITICAL | isolated install root | mechanics + secure first launch | mechanics PASS؛ security FAIL | installer smoke | FAIL | DEF-RC1-001 |
| A11Y-001 | Basic keyboard/labels | MEDIUM | real Chromium | Enter/Tab sensible; no serious blocker | Enter login and Tab order PASS؛ autocomplete LOW | Playwright evidence | PASS | DEF-RC1-008 |
| LOC-001 | French/Arabic/Unicode/date/decimal | HIGH | 10 varied records + documents | round-trip without corruption | UI/DB search and docs PASS for samples | HTTP/DB/PDF evidence | PASS | |
| MUT-001 | Focused mutation score | HIGH | compatible mutation runner | score measured | tool unavailable; no score fabricated | tooling inventory | NOT TESTED | |
| PRINT-001 | Physical printer path | MEDIUM | configured printer | printed page verified | no printer/output capture available | limitation | NOT TESTED | |
| UAT-001 | Business owner acceptance | CRITICAL | authorized business owner | signed UAT | no business owner present | `UAT_CHECKLIST.md` | NOT TESTED | |

## ملحق إغلاق RC-2 المالي

| Test Case ID | المتطلب / الميزة | Risk | Preconditions / Test Data | Expected Result | Actual Result | Evidence | Status | Defect |
|---|---|---|---|---|---|---|---|---|
| FIN-RC2-001 | 25 Golden approved | CRITICAL | Decimal oracle، معدلات وقيم متنوعة | 25/25 truncation + TVA after RG | 25/25 | `tests/test_financial_rules_rc2.py` | PASS | DEF-RC1-002 |
| FIN-RC2-002 | Property/generated | CRITICAL | 2,500 حالة، seed `20260825` | صفر discrepancy | 2,500/2,500 | `tests/test_financial_rules_rc2.py` | PASS | DEF-RC1-002 |
| FIN-RC2-003 | حالات truncation الصريحة | CRITICAL | 12.349/12.345/12.999/100.005/-12.999 | 12.34/12.34/12.99/100.00/-12.99 | مطابق | `tests/test_billing_rules.py` | PASS | DEF-RC1-002 |
| FIN-RC2-004 | FIN-G-06/07/19 | CRITICAL | الحالات الأصلية الفاشلة | مطابق للقاعدة المعتمدة | 3/3 | targeted + full pytest | PASS | DEF-RC1-002 |
| INV-RC2-001 | 10 فواتير E2E | CRITICAL | 5×5/19 ثم 5×10/20؛ قيم متنوعة | 10/10 lifecycle/restart/docs | 10/10 | `tests/test_financial_e2e_rc2.py`; `remediation/evidence/financial_rc2/e2e_10/` | PASS | DEF-RC1-002/003 |
| SNAP-RC2-001 | Scenarios A–E | CRITICAL | تغيير defaults بعد Invoice A | A=5/19، B=10/20 بعد restart/docs | مطابق | E2E + DB/PDF/XLSX | PASS | DEF-RC1-003 |
| SNAP-RC2-002 | Evidence cleanliness reconciliation | CRITICAL | الأدلة النهائية المفصولة عن PRE-FIX | UI=DB=PDF=XLSX والعناوين تطابق snapshot | 10/10 PASS | `remediation/evidence/POST_FIX_DEF-RC1-003_RECONCILIATION.json`; `remediation/evidence/financial_rc2/e2e_10/` | PASS | DEF-RC1-003 |
| DOC-RC2-001 | XLSX exact literals | CRITICAL | PU ومبالغ حساسة لـfloat | Open XML exact؛ لا `78.40000000000001` | `12.34`, `78.40` exact | tests + artifact visual inspection | PASS | DEF-RC1-002/003 |
| DOC-RC2-002 | PDF reconciliation | CRITICAL | 10 فواتير + تاريخيتان | labels/HT/RG/after/TVA/TTC تطابق | مطابق نصيًا وبصريًا | PDFs + rendered inspection | PASS | DEF-RC1-002/003 |
| MIG-RC2-001 | Original-copy v17→v19 | CRITICAL | نسخة read-only المصدر، 5 فواتير | preserve + backfill5/19 + integrity | PASS | `remediation/evidence/financial_rc2/historical_migration.json` | PASS | DEF-RC1-003 |
| MIG-RC2-002 | Real installer 2.5.1→2.5.2 | CRITICAL | invoice marker بقيم معروفة | schema19، 5/19، no corruption | PASS | `remediation/evidence/build_rc2/upgrade-smoke.json` | PASS | DEF-RC1-003 |
| INST-RC2-001 | Fresh install | CRITICAL | isolated installer root/user-data | empty DB، schema19، secure setup | PASS | `remediation/evidence/build_rc2/installer-smoke.json` | PASS | DEF-RC1-001/003 |
| REG-RC2-001 | Full strict suite | CRITICAL | isolated `basetemp`, warnings errors | كل الاختبارات PASS | 136/136 | final console evidence | PASS | all |
| VIS-RC2-001 | Visual strict | HIGH | 16 صفحات Playwright | فوق threshold | 16/16 | `remediation/evidence/build_rc2/visual-regression-report.html` | PASS | regression |
| BUILD-RC2-001 | Release artifacts | CRITICAL | version2.5.2 | build + hashes | PASS 3/3 | `remediation/evidence/build_rc2/SHA256SUMS.txt` | PASS | release |
