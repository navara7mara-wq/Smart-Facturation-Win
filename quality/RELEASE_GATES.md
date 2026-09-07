# بوابات إصدار RC-1

| البوابة | الشرط الإلزامي | الحالة | الدليل / السبب |
|---|---|---|---|
| تعريف RC وحماية بيانات الأعمال | الحالة الدقيقة مسجلة ولا تغيير منطقي للبيانات | PASS | `evidence/RC1_BASELINE.md`؛ hashes كود الإنتاج متطابقة. تغيّر hash SQLite الفيزيائي لكن مقارنة canonical لكل المخطط والصفوف والقيم أثبتت التطابق المنطقي الكامل في `evidence/RC_PROTECTION_VERIFICATION.md` |
| Critical defects | صفر غير محلول | FAIL | `DEF-RC1-001` مفتوح |
| High defects | صفر غير محلول | FAIL | `DEF-RC1-002` إلى `DEF-RC1-005` مفتوحة |
| Critical workflow coverage | 100% من الخطة الحرجة | PASS | 25 فاتورة، lifecycle/auth/import/migration/backup/build/install؛ النتائج قد تفشل لكن التنفيذ تم |
| Financial Golden Cases | 100% PASS | FAIL | 22/25 E2E و22/25 fixed Golden؛ 3 failures |
| Persistence/restart | PASS | PASS | 10 records بعد restart، العلاقات والمجاميع سليمة |
| Data integrity | PASS | PASS | `integrity_check=ok`, صفر FK/orphans/duplicates/mismatches |
| Production build | PASS | PASS | Desktop/Publisher/ZIP/Installer/SHA manifest بُنيت بنجاح |
| No unresolved data-loss risk | لا خطر مفتوح | FAIL | destructive GET في `DEF-RC1-004` |
| No unresolved corruption/accuracy risk | لا خطر مفتوح | FAIL | rounding وdocument labels وclient address |
| No unexplained intermittent critical failure | لا فشل حرج متقطع | PASS | الإخفاقات حتمية وقابلة للإعادة، لا intermittent مجهول |
| Regression | PASS | FAIL | الاختبارات القائمة 79/79 وvisual 16/16، لكن اختبارات RC الحرجة الجديدة فشلت |
| Clean Install | PASS عند وجود المثبّت | FAIL | lifecycle التقني PASS، لكن الحساب الافتراضي الحرج يفشل بوابة الأمان |
| Backup/Restore | PASS لأن الميزة موجودة | PASS | round-trip فعلي مع قيمة معروفة وDB integrity |
| Migration/Upgrade | PASS لأن إصدارات سابقة موجودة | PASS | ترقية مثبت 2.5.0→2.5.1 مع business marker محفوظ |
| Security blocker | لا Critical/High | FAIL | default super-admin وdestructive GET |
| Accessibility/localization | لا blocker خطير | PASS | Enter/Tab وUnicode/العربية والفرنسية نجحت؛ ملاحظة LOW للـautocomplete |
| Mutation testing | نتيجة مقاسة أو قيد موثق | NOT TESTED | `mutmut`/أداة متوافقة غير متاحة؛ لم تُحوَّل إلى PASS |
| UAT business sign-off | موافقة مالك الأعمال | NOT TESTED | لا يوجد ممثل أعمال في جلسة التدقيق |

**النتيجة الحاكمة:** FAIL. الدرجة المعلوماتية لا تتجاوز هذه البوابات.

---

# بوابات إكمال معالجة RC-2 — 2026-08-26

يبقى القسم السابق سجلًا تاريخيًا لحالة RC-1. الحالات التالية تخص RC-2 بعد القرارات المالية المعتمدة.

| البوابة | الحالة | الدليل |
|---|---|---|
| صفر CRITICAL غير محلول | PASS | `DEF-RC1-001=FIXED` واختبارات auth/installer/upgrade |
| صفر HIGH غير محلول | PASS | `DEF-RC1-002` إلى `DEF-RC1-005 = FIXED` في `remediation/DEFECT_STATUS_RC2.md` |
| Golden financial | PASS | 25/25 وفق Oracle مستقل يستخدم Decimal + truncation |
| Property financial | PASS | 2,500/2,500 حالة مولدة، seed ثابت `20260825` |
| FIN-G-06 / FIN-G-07 / FIN-G-19 | PASS | regression مخصص في `tests/test_billing_rules.py` |
| TVA على HT après RG | PASS | Golden + property + 10 E2E |
| Snapshot نسب الفاتورة | PASS | A–E، restart، 5/19 و10/20، DB/UI/PDF/XLSX |
| Historical migration v17→v19 | PASS | 5/5 فواتير محفوظة؛ backfill 5/19؛ القيم والأسطر والعلاقات ثابتة |
| Fresh install schema/first run | PASS | schema 19، الحقول موجودة، First Run آمن، runtime checks |
| Upgrade 2.5.1→2.5.2 | PASS | marker وفاتورة تاريخية وقيمها محفوظة؛ backfill 5/19؛ integrity ok |
| 10 invoices E2E | PASS | 10/10 create/edit/save/reopen/restart/DB/PDF/XLSX |
| Persistence/data integrity | PASS | integrity ok، FK/orphans/duplicates=0، restart PASS |
| Security regression | PASS | auth/CSRF/routes/uploads/headers/roles ضمن 136/136 |
| Full strict regression | PASS | 136/136، `-W error`، 129.38 ثانية |
| Visual regression | PASS | 16/16؛ أدنى تشابه 99.800% |
| Build RC-2 | PASS | Desktop/Portable/Installer/Publisher + SHA-256 manifest |
| Clean install/update/uninstall | PASS | `remediation/evidence/build_rc2/installer-smoke.json` |
| Backup/Restore | PASS | regression suite + QA isolation |
| Original business DB protected | PASS | SHA-256 قبل/بعد `7733604A...F601` |
| Historical defect register preserved | PASS | SHA-256 `40924A1D...2EB3` |

**نتيجة بوابة المعالجة:** PASS للانتقال إلى Independent Final Certification فقط. هذا لا يمثل Final Production Certification.
