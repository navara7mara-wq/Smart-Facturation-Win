# تقرير تدقيق الجاهزية للإنتاج — RC-1

## الملخص التنفيذي

أُجري تدقيق مستقل للحالة الحالية باعتبارها **RC-1** ومن دون تعديل كود الإنتاج. البنية العامة، سلامة قاعدة البيانات، الاستمرارية، النسخ الاحتياطي/الاستعادة، البناء، التثبيت، والترقية أظهرت أساسًا تقنيًا قويًا. نجحت الاختبارات الآلية القائمة وعددها 79، ونجحت 16 مقارنة بصرية صارمة، وبُنيت الحزمة النهائية وتطابقت بصماتها.

مع ذلك، لا تستوفي النسخة بوابات الإصدار الإلزامية بسبب عيب أمني CRITICAL وأربعة عيوب HIGH: حساب إداري افتراضي ثابت بلا تغيير إلزامي، فروق تقريب مالية، نسب خاطئة في عناوين PDF/XLSX عند تغيير الإعدادات، حذف عبر GET بلا CSRF، وعدم حفظ عنوان العميل عند التعديل. لذلك لا يجوز الانتقال إلى Final Certification في الحالة الحالية.

## تعريف النسخة التي تم اختبارها

- تسمية التدقيق: **RC-1 — Release Candidate 1**.
- الإصدار المعلن: `2.5.1`.
- الفرع: `master`.
- Commit baseline: `965f0f81a368d3557a187f57424c87bff0c3298a` — `Consolidate admin pages into settings`.
- التاريخ: 2026-08-25، المنطقة الزمنية Africa/Algiers.
- المستودع كان dirty عند البداية: 45 ملفًا tracked معدلاً، إضافة إلى ملفات untracked؛ لذلك commit وحده لا يعرّف RC-1.
- الحالة الدقيقة محفوظة في `quality/evidence/RC1_BASELINE.md` و`git_status_initial.txt`.
- تطابقت hashes النهائية مع الابتدائية للملفات الإنتاجية المفتاحية الثمانية؛ لم يُعدَّل سلوك التطبيق أثناء التدقيق.
- كل الاختبارات المدمرة للأعمال استخدمت `tmp/rc1-audit/data/QA_RC1.sqlite3` وسجلات تبدأ بـ`QA_RC1_`؛ لم يتغير أي صف أو حقل أو علاقة أو مخطط في قاعدة الأعمال الأصلية.
- تغيرت بصمة ملف SQLite الأصلي الفيزيائية من `ad8be2...` إلى `9afc2c...` أثناء تشغيل الاختبارات الآلية، مع ثبات الحجم. أثبتت مقارنة canonical كاملة للمخطط ولكل صف وقيمة و`sqlite_sequence` مع نسخة QA سابقة للتغيير عدم وجود أي اختلاف منطقي، ونجح `integrity_check` و`foreign_key_check`. التفاصيل في `quality/evidence/RC_PROTECTION_VERIFICATION.md`.
- أنشأت اختبارات backup ستة ملفات QA في مجلد `backups/` العام بدل توجيه المخرجات كلها إلى مجلد العزل. احتُفظ بها كدليل ولم تُحذف؛ وهي ليست سجلات أعمال أو نسخًا أنشأها المستخدم.

## نطاق الاختبار

شمل النطاق اكتشاف البنية والميزات، التطبيق الفعلي HTTP/desktop، SQLite/migrations، CRUD والتكرار، الحسابات والمستندات، lifecycle والصلاحيات، imports/exports، النسخ الاحتياطي والاستعادة، الأداء والحجم، الأمن، البناء، التثبيت النظيف، والترقية 2.5.0→2.5.1. شملت الأدلة UI/HTTP/DB/PDF/XLSX/installer، ولم تعتمد أي نتيجة PASS على النقر وحده.

لم يشمل النطاق Penetration Test خارجيًا، طباعة ورقية فعلية، soak متعدد الساعات، Mutation Score، أو توقيع UAT من مالك أعمال.

## الصفحات والميزات المكتشفة

البنية: `pywebview` في `desktop.py`، خادم `BaseHTTPRequestHandler/ThreadingHTTPServer` في `app.py` على loopback/random port، HTML مولد من الخادم مع JavaScript/CSS، SQLite في `db.py` و17 migration، OpenPyXL/XLSX، Node/Playwright/PDF، PyInstaller، Inno Setup، Ed25519 licensing.

الصفحات الأساسية المكتشفة: `/`, `/table-facturation-new`, `/table-facturation`, `/invoices`, `/purchase-orders`, `/clients`, `/bpu`, `/settings`, `/company`, `/company-branches`, `/templates`, `/users`, `/backup`, `/license`, `/about`, `/status`.

الميزات: Dashboard/KPI، Table Facturation والبحث/الفرز/التصفية/pagination، العملاء واتجاهاتهم، أوامر الشراء والمواقع والمرفقات، BPU/ST mapping/versioning/import، الفواتير العادية وNDC وحالاتها، PDF/XLSX/DQ/DE، القوالب والنسب المالية، الصلاحيات، النسخ الاحتياطي والاستعادة، الترخيص، status/help، والحزم المحمولة والمثبّت. الجرد الكامل في `quality/FEATURE_INVENTORY.md`.

## تحليل المخاطر

- **CRITICAL:** التثبيت/التشغيل، حساب super-admin الأولي، الفواتير والحسابات، BPU mapping، lifecycle، DB/migrations، المستندات المالية، backup/restore، build/install/upgrade.
- **HIGH:** العملاء والاتجاهات، أوامر الشراء والمواقع، imports/exports، Dashboard، Table Facturation، القوالب والنسب، الصلاحيات، Unicode/localization.
- **MEDIUM:** الأداء، headers، upload hardening، keyboard/accessibility، status/help.
- **LOW:** توافق مديري كلمات المرور وملاحظات واجهة غير حاجبة.

## مصفوفة التغطية

| الميزة / العملية | مستوى الخطورة | Test Cases | عدد التنفيذات | ناجح | فاشل | لم يُختبر | الحالة |
|---|---|---:|---:|---:|---:|---:|---|
| تعريف RC وقاعدة البيانات الأصلية | CRITICAL | 2 | 2 | 2 | 0 | 0 | PASS |
| الصفحات والمقارنة البصرية | HIGH | 16 | 16 | 16 | 0 | 0 | PASS |
| العملاء: إنشاء/تعديل | HIGH | 2 | 20 | 10 | 10 | 0 | FAIL |
| اتجاهات العملاء: إنشاء/تعديل | HIGH | 2 | 20 | 20 | 0 | 0 | PASS |
| أوامر الشراء: إنشاء/تعديل/إعادة فتح | CRITICAL | 2 | 20 | 20 | 0 | 0 | PASS |
| المواقع: إنشاء/تعديل/إعادة فتح | HIGH | 2 | 20 | 20 | 0 | 0 | PASS |
| الفواتير والحسابات E2E | CRITICAL | 3 | 25 | 22 | 3 | 0 | FAIL |
| Golden fixed calculations | CRITICAL | 1 | 25 | 22 | 3 | 0 | FAIL |
| Property-generated calculations | CRITICAL | 1 | 2000 | 1527 | 473 | 0 | FAIL |
| البحث القانوني في Table Facturation | HIGH | 1 | 10 | 10 | 0 | 0 | PASS |
| Save المكرر/duplicate submission | CRITICAL | 1 | 2 | 2 | 0 | 0 | PASS |
| المستندات الفعلية PDF/XLSX | CRITICAL | 4 | 10 | 8 | 2 | 0 | FAIL |
| restart persistence | CRITICAL | 1 | 10 | 10 | 0 | 0 | PASS |
| backup/restore round-trip | CRITICAL | 1 | 1 | 1 | 0 | 0 | PASS |
| migration tests + real upgrade | CRITICAL | 6 | 6 | 6 | 0 | 0 | PASS |
| الاختبارات الآلية الكاملة | CRITICAL | 79 | 79 | 79 | 0 | 0 | PASS |
| build artifacts/hash manifest | CRITICAL | 5 | 5 | 5 | 0 | 0 | PASS |
| clean install mechanics/security | CRITICAL | 5 | 5 | 4 | 1 | 0 | FAIL |
| الطباعة الورقية | MEDIUM | 1 | 0 | 0 | 0 | 1 | NOT TESTED |
| Mutation Testing | HIGH | 1 | 0 | 0 | 0 | 1 | NOT TESTED |
| UAT business sign-off | CRITICAL | 1 | 0 | 0 | 0 | 1 | NOT TESTED |

ملاحظة: في property testing نُفذت 2,000 توليفة كاملة وفشلت 473؛ حُفظت التفاصيل الكاملة لأول 20 مخالفة لتقييد حجم الدليل. لا يعني نجاح البقية اعتماد القاعدة قانونيًا قبل تأكيد سياسة التقريب.

## الاختبارات الحرجة

نُفذت 25 حالة فاتورة متنوعة: تواريخ مختلفة، 1–5 أسطر، كميات صغيرة وكبيرة وعشرية، أسعار مختلفة، إنشاء/حفظ/إعادة فتح/تحرير وإعادة حفظ. نجحت دورة البيانات، لكن 3 حالات فشلت في المطابقة المالية بسنت واحد. اختبارات lifecycle منعت التحولات غير الصحيحة وقفل الفاتورة الصادرة وسمحت بالاستعادة المصرح بها، وكلها PASS ضمن suite.

## الاختبارات المالية

القواعد المنفذة المكتشفة: مجموع HT من مبالغ الأسطر، retention من HT، TVA من HT بعد retention، ثم TTC. المعدلات الافتراضية 5% و19% وقابلة للتعديل. القاعدة القانونية الدقيقة للتقريب ومبدأ تطبيق TVA بعد retention موسومان **BUSINESS RULE REQUIRES CONFIRMATION**.

فشلت الحالات `FIN-G-06`, `FIN-G-07`, `FIN-G-19` عند حدود `0.005/0.015/0.025`. كما فشلت 3 حالات مالية فعلية ذات مبالغ متوسطة/كبيرة، وفشلت 473/2,000 توليفة property-based بسبب تمثيل `float`. لا يمكن منح Golden Gate حالة PASS.

## اختبار التكرار

- 10 إنشاءات متنوعة لكل من العميل والاتجاه وأمر الشراء والموقع والفاتورة الأولى.
- 10 تعديلات لكل كيان: الاتجاهات/أوامر الشراء/المواقع PASS؛ عناوين العملاء FAIL 10/10.
- 25 إنشاء/تحرير/إعادة فتح للفواتير إجمالًا.
- 10 searches مختلفة على `/table-facturation-new`: PASS 10/10.
- Save مكرر لنفس الفاتورة: بقي سجل واحد.
- 8 مخرجات مستندات default + مخرجان بمعدلات غير افتراضية.

## اختبار قاعدة البيانات وسلامة البيانات

قاعدة QA النهائية: 610 BPU، 1,132 ST، 60 أمر شراء، 60 موقعًا، 49 فاتورة، 148 سطر فاتورة. النتيجة: `PRAGMA integrity_check=ok`، صفر FK violations، صفر orphan lines/PO/site، صفر duplicate invoice/PO numbers، صفر line-total mismatch، وصفر TTC reconciliation mismatch وفق القيم المخزنة. هذا لا يلغي عيب oracle الخارجي؛ DB متسقة داخليًا مع ناتج التطبيق الخاطئ بالسنت.

بالنسبة لقاعدة الأعمال الأصلية، تطابقت جميع الجداول والصفوف والقيم والمخطط وتسلسلات SQLite منطقيًا مع نسخة سابقة لتغير البصمة، رغم اختلاف SHA-256 الفيزيائي للملف. لم يُثبت فقد أو تعديل لبيانات الأعمال. مع ذلك، فإن إنشاء اختبارات backup لملفاتها داخل `backups/` العام بدل مساحة QA المعزولة قيد في بنية عزل الاختبارات، وقد وُسمت تلك الملفات بوضوح في دليل الحماية.

## اختبار الحفظ والاستمرارية

بعد restart بقيت 10 فواتير QA الأولى: 9 نشطة وواحدة soft-deleted، وتوافق سلوك إعادة الفتح مع الحالة، وتصالحت الأسطر وTTC. لا فقد أو duplication في المسار المختبر. النسخ الاحتياطي أعاد عنوانًا معدلًا عمدًا إلى قيمته المعروفة وحافظ على integrity/FK.

## اختبار الحالات الحدية والسلبية

شملت empty/invalid invoice lines، NDC constraints، invalid lifecycle dates، duplicate imports، unknown references، wrong password/lockout، tampered/wrong-machine licenses، invalid SQLite restore، decimal boundaries، repeated submit، CSRF invalid، وdestructive GET. السلبيات المغطاة آليًا PASS، باستثناء destructive GET والحسابات العشرية الموثقة كعيوب.

## اختبار الملفات والتقارير

فُتحت ملفات XLSX فعليًا وفُحصت الخلايا والنطاقات والصيغ، ورُسمت صفحات PDF الست نصيًا وبصريًا. العينتان default (Normal وNDC) تطابقتا مع DB وظهرت الشعارات بعد توفير ملفات uploads المقابلة في QA. لم توجد أسطر مفقودة أو clipping في العينات.

اختبار rate=10%/20% أثبت أن القيم صحيحة، لكن العناوين بقيت 5%/19% في PDF وXLSX؛ لذلك بوابة دقة المستند FAIL. النطاقات المستخدمة الكبيرة في template (`963×24`, `1000×25`) ملاحظة أداء وليست خطأ ماليًا مثبتًا.

## اختبارات الأداء والاستقرار

Median للصفحات الأساسية بين 13.36 و31.85 ms؛ P95 الأقصى 42.52 ms. XLSX نحو 2.78s وPDF نحو 4.88s لعينة واحدة. أول تشغيل المثبّت 8.15s، والتشغيل بعد update 8.75s. بعد 70 طلبًا وتوليد مستندات انخفض Working Set بنحو 0.5 MB؛ لم يظهر leak واضح في النافذة القصيرة. soak متعدد الساعات NOT TESTED. التفاصيل في `quality/PERFORMANCE_BASELINE.md`.

## الاختبارات الأمنية

الإيجابيات: loopback/random port، PBKDF2، server sessions، HttpOnly/SameSite، lockout، parameterized SQL، path containment، CSRF للـPOST، Ed25519/machine binding، استبعاد المفتاح الخاص من الحزمة، و0 npm vulnerabilities.

الإخفاقات: default super-admin بلا تغيير إلزامي، destructive GET، غياب headers، وغياب upload/request size/content validation العامة. النطاق عملي ومحدود وليس شهادة أمن.

## نتائج Automated Tests

- `python -m pytest -q -W error`: **79 passed in 331.68s**.
- Unit: 20 PASS؛ Integration: 18 PASS؛ E2E: 2 PASS؛ Migration marker: 5 PASS؛ unmarked: 34 PASS.
- Visual strict: 16/16 PASS، أدنى similarity = 99.802%، دون console/overflow failure في suite.
- `compileall`: PASS؛ `pip check`: PASS؛ `npm audit`: 0 vulnerabilities.
- `ruff`, `mypy`, `bandit`, `pip-audit`, `mutmut`: غير متاحة. لم تُحوَّل إلى PASS.

## نتائج Build

نجح `scripts/build_release.ps1 -Version 2.5.1` في بناء Desktop، License Studio، Portable ZIP، Installer و`SHA256SUMS.txt`. طابقت بصمات SHA-256 كل artifacts، ولم يُعثر على private key داخل الحزمة. Build Gate = PASS.

## نتائج Clean Install إن أمكن

التثبيت، first launch smoke، DB/HTTP/Excel/PDF/WebView runtime، reinstall/update، data preservation، uninstall كلها PASS تقنيًا. لكن first-run security FAIL لأن الناتج يؤكد `admin/admin123` بلا تغيير إلزامي. لذلك Clean Install Quality Gate الكلية = FAIL.

## نتائج Backup / Restore إن كانت موجودة

PASS. أُنشئت نسخة SQLite فعلية بحجم 671,744 bytes، نُزلت، عُدلت قيمة QA بعد النسخ، ثم استعيدت النسخة؛ عادت القيمة الأصلية، `integrity_check=ok` وصفر FK violations.

## نتائج Migration / Upgrade إن كانت مطلوبة

PASS للنطاق المنفذ. نجحت 5 اختبارات migration/rollback/append-only. ثُبت `2.5.0` فعليًا، وأضيف client marker `QA_RC1_UPGRADE_250_TO_251`، ثم ثُبت `2.5.1` فوقه؛ بقي marker، migration max=17، integrity ok، وصفر FK violations، وبقيت DB بعد uninstall.

## المشاكل المكتشفة

| Defect | Severity | الملخص | الحالة |
|---|---|---|---|
| DEF-RC1-001 | CRITICAL | default `admin/admin123` بلا تغيير إلزامي | OPEN |
| DEF-RC1-002 | HIGH | أخطاء تقريب مالية بمقدار سنت | OPEN |
| DEF-RC1-003 | HIGH | عناوين نسب PDF/XLSX ثابتة 5%/19% | OPEN |
| DEF-RC1-004 | HIGH | حذف عبر GET بلا CSRF | OPEN |
| DEF-RC1-005 | HIGH | عنوان العميل لا يُحفظ عند التعديل | OPEN |
| DEF-RC1-006 | MEDIUM | uploads بلا حدود/فحص محتوى عام | OPEN |
| DEF-RC1-007 | MEDIUM | security headers غائبة | OPEN |
| DEF-RC1-008 | LOW | password autocomplete غائب | OPEN |

التفاصيل الكاملة وخطوات الإعادة والتوصيات ومتطلبات retest في `quality/DEFECT_REGISTER.md`.

## Release Blockers

عدد العوائق المفتوحة: 5 — عائق CRITICAL وأربعة HIGH. أي واحد منها يكفي لفشل الأهلية، ولا توجد remediation ضمن هذه المرحلة.

## الأمور التي لم يمكن اختبارها

- الطباعة على جهاز فعلي ومطابقة الورق.
- Mutation Score بأداة متوافقة.
- UAT وتوقيع مالك الأعمال.
- soak/resource monitoring متعدد الساعات واختبارات multi-user concurrency واسعة.
- اختبار اختراقي خارجي وdependency audit Python عبر `pip-audit`.
- مصفوفة يدوية شاملة لكل route×role؛ غطت الاختبارات الحالية المسارات الحساسة الأساسية فقط.
- لم يكن ممكنًا تفسير اختلاف bytes الداخلي لملف SQLite الأصلي على مستوى صفحات التخزين؛ عُوّض ذلك بمقارنة canonical كاملة أثبتت التطابق المنطقي لكل البيانات والمخطط.

## المخاطر المتبقية

تبقى قواعد أعمال تحتاج تأكيدًا: طريقة التقريب القانونية، أساس TVA، قابلية تغيير النسب لكل عقد/فاتورة، سياسات أرقام الفواتير، المحتوى القانوني الإلزامي، وسياسة restore للـsoft deletion. كما أن version ranges في Python وcaret Playwright تقلل reproducibility مقارنة بقفل dependencies كامل.

## Quality Gates

| Quality Gate | الحالة |
|---|---|
| 0 unresolved CRITICAL | FAIL |
| 0 unresolved HIGH | FAIL |
| 100% CRITICAL workflows executed | PASS |
| CRITICAL financial Golden Tests = 100% PASS | FAIL |
| Persistence | PASS |
| Data Integrity | PASS |
| Production Build | PASS |
| No unresolved data-loss risk | FAIL |
| No unresolved corruption/accuracy risk | FAIL |
| No unexplained intermittent critical failure | PASS |
| Regression ultimately PASS | FAIL |
| Clean Install | FAIL |
| Backup / Restore | PASS |
| Migration / Upgrade | PASS |
| Accessibility / Localization serious blockers | PASS |
| Mutation Testing | NOT TESTED |
| UAT | NOT TESTED |
| Mandatory Release Blockers CLOSED | FAIL |

## درجة الجاهزية

**Production Readiness Score: 65/100**

الدرجة معلوماتية فقط. قوة البناء والاختبارات والاستمرارية لا تتجاوز فشل البوابات الأمنية والمالية وبيانات العملاء.

# Release Blockers التي يجب إصلاحها

1. `DEF-RC1-001`: إزالة بيانات الدخول الإدارية الافتراضية الثابتة وفرض تهيئة آمنة لأول تشغيل.
2. `DEF-RC1-002`: اعتماد حساب عشري وقاعدة تقريب معتمدة وإغلاق كل فروق السنت.
3. `DEF-RC1-003`: ربط نسب PDF/XLSX بالمعدلات الفعلية المحفوظة للفواتير.
4. `DEF-RC1-004`: منع كل state-changing GET وتطبيق CSRF/lifecycle/idempotency.
5. `DEF-RC1-005`: حفظ عنوان العميل فعليًا في مسار التعديل والتحقق منه بعد restart والمستندات.

# 🔴 النسخة غير مؤهلة حاليًا للانتقال إلى Final Certification
