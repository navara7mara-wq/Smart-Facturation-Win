# تقرير إكمال المعالجة المنضبطة RC-1 → RC-2

## الملخص التنفيذي

أُغلقت المعالجة الفنية والمالية المطلوبة لـRC-2 بعد اعتماد مالك العمل لقواعد الاختزال النقدي ووعاء TVA وسياسة snapshot. أصبح `DEF-RC1-002` و`DEF-RC1-003` بحالة **FIXED** بعد اجتياز شروط الإغلاق كاملة: 25/25 Golden، 2,500/2,500 generated/property، عشر فواتير E2E، migration تاريخية، Fresh Install، Upgrade، ومطابقة UI/DB/PDF/XLSX.

ملاحظة نظافة الأدلة (2026-08-26): صُنفت ملفات `rate_10_20.*` القديمة رسميًا كأدلة PRE-FIX FAIL ونُقلت إلى `quality/remediation/evidence/pre_fix/DEF-RC1-003/`. الدليل المرجعي بعد الإصلاح هو مجموعة `financial_rc2/e2e_10/`، وقد أعيدت مطابقتها مستقلًا بنتيجة 10/10 PASS عبر UI/DB/PDF/XLSX.

جميع العيوب الثمانية في baseline RC-1 لها حالة معالجة FIXED، ويوجد صفر CRITICAL وصفر HIGH غير محلول. لا يُصدر هذا التقرير Final Production Certification؛ أقصى استنتاج له هو أهلية RC-2 للانتقال إلى فحص نهائي مستقل.

## القواعد المالية المعتمدة

1. لا تقريب نقدي؛ كل مبلغ رسمي يُختزل إلى منزلتين عشريتين باتجاه الصفر.
2. الحساب الرسمي يستخدم `Decimal` بدقة وسيطة مرتفعة، دون `float` أو `round`.
3. TVA تُحسب على `HT après RG`.
4. كل فاتورة تحفظ `rg_rate` و`tva_rate` عند الإنشاء.
5. الإعدادات العامة defaults للفواتير الجديدة فقط.
6. كل فاتورة تاريخية قبل migration v19 تُملأ بـRG5% وTVA19%.
7. PDF/XLSX والمعاينات تستخدم snapshot الفاتورة للقيم والعناوين.

## التعديلات المنفذة

- إضافة `services/money.py` كمصدر مركزي لـDecimal والتحويل والاختزال والتنسيق.
- تحويل محرك الحساب والحفظ من الحساب الثنائي إلى Decimal، وتطبيق التسلسل HT/RG/HT après RG/TVA/TTC.
- تحويل الحساب التفاعلي في واجهة الفاتورة إلى scaled decimal باستخدام `BigInt` بدل `Number` للمبالغ الرسمية.
- migration v19 تضيف `rg_rate` و`tva_rate` مع constraints وbackfill5/19، وتعمل idempotently على fresh/upgrade.
- التقاط rates عند إنشاء الفاتورة وحمايتها من التغيير اللاحق، واستخدامها عند reopen/edit/export.
- إزالة labels الثابتة من PDF/XLSX، وفرض snapshot حتى مع templates مخصصة قديمة.
- منع Open XML من المرور عبر float؛ القيم النقدية في XLSX محفوظة كـnumeric literals دقيقة.

## حالة العيوب

| Defect | الخطورة | الحالة قبل الإصلاح | الحالة بعد الإصلاح | Retest | Regression |
|---|---|---|---|---|---|
| DEF-RC1-001 | CRITICAL | OPEN / Blocker | FIXED | PASS | PASS |
| DEF-RC1-002 | HIGH | OPEN / Blocker | FIXED | PASS | PASS |
| DEF-RC1-003 | HIGH | OPEN / Blocker | FIXED | PASS | PASS |
| DEF-RC1-004 | HIGH | OPEN / Blocker | FIXED | PASS | PASS |
| DEF-RC1-005 | HIGH | OPEN / Blocker | FIXED | 10/10 + restart | PASS |
| DEF-RC1-006 | MEDIUM | OPEN | FIXED | PASS | PASS |
| DEF-RC1-007 | MEDIUM | OPEN | FIXED | PASS | PASS |
| DEF-RC1-008 | LOW | OPEN | FIXED | PASS | PASS |

لم يُحذف أو يُخفّض أو يُعدّل السجل التاريخي `quality/DEFECT_REGISTER.md`؛ بقي SHA-256=`40924A1D...2EB3`.

## DEF-RC1-002

- **الخطورة السابقة:** HIGH / Release Blocker.
- **السبب الجذري:** استعمال `float` و`round` عبر الحساب والحفظ والعرض والتصدير، ما أدى إلى فروق سنت وعدم حتمية.
- **الملفات المعدلة:** `services/money.py`, `services/billing.py`, `services/bpu_mapping.py`, `app.py`, `services/invoice_exports.py`, `services/xlsx.py`, `scripts/export_invoice_template.py` والاختبارات.
- **الإصلاح:** Decimal مركزي، truncation باتجاه الصفر، TVA على HT après RG، canonical storage، حساب UI دون float، وXLSX exact literals.
- **الاختبارات الجديدة:** 25 Golden ثابتة، 2,500 generated/property، regression لقيم truncation وFIN-G-06/07/19، و10 invoice E2E.
- **عدد التنفيذات:** 25 Golden +2,500 generated +10 E2E، إضافة إلى full regression.
- **النتيجة:** PASS؛ لا discrepancy غير مفسر ولا فرق 0.01.
- **الأدلة:** `tests/test_financial_rules_rc2.py`, `tests/test_financial_e2e_rc2.py`, `quality/remediation/evidence/financial_rc2/`.
- **المخاطر المتبقية:** لا عيب مالي معروف ضمن القواعد المعتمدة؛ يلزم Final Certification مستقل.
- **الحالة النهائية:** FIXED.

## DEF-RC1-003

- **الخطورة السابقة:** HIGH / Release Blocker.
- **السبب الجذري:** labels ثابتة 5/19 وعدم وجود rates على مستوى الفاتورة.
- **الملفات المعدلة:** `database/migrations.py`, `app.py`, `services/invoice_lifecycle.py`, `services/invoice_exports.py`, `scripts/export_invoice_template.py` والاختبارات.
- **الإصلاح:** schema v19 وbackfill5/19، snapshot عند الإنشاء، historical immutability، واستخدام snapshot في UI/PDF/XLSX.
- **الاختبارات الجديدة:** scenarios A–E، migration original-copy، fresh schema، installer upgrade، restart، PDF/XLSX لكل من5/19 و10/20.
- **عدد التنفيذات:** 10 فواتير كاملة، 5 فواتير تاريخية migrated، 5 XLSX تاريخية، 2 PDF تاريخية، fresh وupgrade.
- **النتيجة:** PASS؛ تغيير defaults لا يغير الفواتير القائمة.
- **الأدلة:** `historical_migration.json`, `e2e_10/`, `upgrade-smoke.json`.
- **المخاطر المتبقية:** لا عيب snapshot معروف؛ يلزم Final Certification مستقل.
- **الحالة النهائية:** FIXED.

## نتائج Golden Tests

**25/25 PASS**. تشمل كميات وأسعارًا صغيرة وكبيرة وحدودًا عشرية، سطرًا واحدًا ومتعدد الأسطر، ومعدلات RG/TVA افتراضية وغير افتراضية. كل expected value حُسب بأوراكل Decimal مستقل.

## نتائج Property-Based Tests

**2,500/2,500 PASS**، seed=`20260825`. تم التحقق من الحتمية والاختزال واتساق RG وHT après RG ووعاء TVA والمصالحة النهائية.

## نتائج اختبار 10 فواتير End-to-End

**10/10 PASS**: create/save/reopen/edit/save/restart/reopen/DB/PDF/XLSX. خمس فواتير5/19 وخمس10/20، ثم defaults7/21 دون تغيير snapshots السابقة.

## نتائج Snapshot لنسب RG/TVA

Scenarios A–E: **PASS**. Invoice A بقيت5/19، Invoice B أخذت10/20، واستمرتا بعد restart، وظهرت القيم والعناوين الصحيحة في PDF/XLSX.

## نتائج Migration للفواتير القديمة

على نسخة read-only المصدر ثم QA copy: v17→v19 **PASS**. بقيت 5/5 فواتير، وكل قيمها وأسطرها وعلاقاتها ثابتة، وطبّق backfill5/19. بعد تغيير global defaults إلى10/20 بقيت القديمة5/19. integrity=`ok`, FK/orphans/duplicates=0.

## نتائج Fresh Install

**PASS**: schema19 وحقلا snapshot موجودان، DB business فارغة، First Run آمن، HTTP/XLSX/PDF/WebView تعمل، وإعادة التثبيت وإلغاء التثبيت يحفظان user-data.

## نتائج Upgrade

المثبت الحقيقي2.5.1→2.5.2: **PASS**. فاتورة تاريخية معروفة بقيت بقيم `1000/50/950/180.50/1130.50` وسطرها وعلاقاتها، وأخذت snapshot5/19، ووصل المخطط إلى19 دون FK violations.

## مقارنة UI / DB / PDF / XLSX

**PASS** لـ10/10 فواتير. تطابقت rates وHT وRG وHT après RG وTVA وTTC والعناوين. فُحص PDF نصيًا وبصريًا، وفُحص XLSX بصريًا وداخليًا؛ لا binary-float literal ولا فرق سنت.

## سلامة قاعدة البيانات

`PRAGMA integrity_check=ok`; foreign-key violations=0؛ duplicate invoice numbers=0؛ orphan invoice lines=0؛ الحقول الجديدة موجودة وملأت تاريخيًا؛ restart/reopen=PASS. قاعدة الأعمال الأصلية لم تتغير وبصمتها قبل/بعد `7733604A...F601`.

## نتائج Regression Suite كاملة

- pytest strict: **136/136 PASS** في129.38s، warnings كأخطاء.
- Visual strict: **16/16 PASS**؛ أدنى تشابه99.800%.
- compileall: PASS.
- Authentication, clients, directions, PO, sites, BPU, invoices, persistence, migrations, backup/restore, uploads, security, desktop: مغطاة داخل الحزمة الكاملة وPASS.

## نتائج Build

Desktop وPortable ZIP وInstaller وPublisher: **PASS**. Clean Install وUpgrade: **PASS**. البصمات النهائية مسجلة في `RC2_BUILD_MANIFEST.md`. Authenticode لم يُنفذ لعدم توفر شهادة ناشر معتمدة؛ لا يُعد Release Blocker لهذه المعالجة، ويجب تقييمه في Final Certification حسب سياسة النشر.

## الملفات المعدلة

أهم ملفات المعالجة المالية: `services/money.py`, `services/billing.py`, `services/bpu_mapping.py`, `services/invoice_exports.py`, `services/invoice_lifecycle.py`, `services/xlsx.py`, `database/migrations.py`, `app.py`, `scripts/export_invoice_template.py`. كما حُدثت اختبارات ووثائق QA وscripts الترقية. القائمة المصدرية الكاملة محفوظة في checkpoint Git.

## الاختبارات التي تمت إضافتها

`tests/test_financial_rules_rc2.py`, `tests/test_financial_e2e_rc2.py`، واختبارات v19/fresh/exact XLSX، مع تقوية `test_billing_rules.py`, `test_migrations.py`, `test_invoice_exports.py`, `test_spreadsheets.py` وupgrade marker.

## المشاكل الجديدة المكتشفة أثناء الإصلاح

اكتُشف أن openpyxl يحول `Decimal('78.40')` إلى XML literal `78.40000000000001`. عولج ذلك في الدورة نفسها بإعادة كتابة numeric literals الرسمية مباشرة وباختبار regression؛ النتيجة النهائية PASS.

## المخاطر المتبقية

- لم يُجر Final Production Certification في هذه المرحلة.
- Authenticode غير متاح دون شهادة ناشر.
- القواعد غير المالية المدرجة في قسم «تحتاج تأكيدًا» في `BUSINESS_RULES.md` لم تُفترض ولم تمنع إغلاق العيبين الماليين.

## الأمور التي تحتاج قرار Business Rule

لا يوجد قرار مالي متبقٍ يمنع `DEF-RC1-002/003`. بقيت مسائل مستقلة: سياسة ترقيم الفواتير، المتطلبات القانونية الشاملة للمستند، تفاصيل MGC، وSLA التأخر.

## Release Blockers المتبقية

**لا يوجد.** صفر CRITICAL وصفر HIGH غير محلول في سجل حالة المعالجة.

# ✅ RC-2 جاهز للانتقال إلى Independent Final Certification
