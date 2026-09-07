# سجل عيوب RC-1

جميع العيوب أدناه مُعاد إنتاجها أو مثبتة مباشرة. لم يُعدَّل كود الإنتاج لمعالجتها.

## DEF-RC1-001 — بيانات دخول إدارية افتراضية ثابتة بلا تغيير إلزامي

- **Severity:** CRITICAL
- **Risk:** وصول غير مصرح به إلى كامل البيانات والإعدادات والفواتير والنسخ الاحتياطية.
- **Page/Module:** Clean Install / `desktop.py` / `services/auth.py`
- **Feature / Test Case:** First Run Authentication — `SEC-001`, `INST-001`
- **Preconditions:** تثبيت نظيف للإصدار `2.5.1` ومجلد بيانات مستخدم جديد.
- **Steps to reproduce:** تثبيت المثبّت في مسار QA؛ تشغيل `PhoEniX BPU.exe --smoke-test`؛ فحص حساب `admin` وحقل `must_change_password`.
- **Expected result:** إنشاء سر فريد أو فرض تغيير كلمة المرور الافتراضية قبل السماح باستخدام التطبيق.
- **Actual result:** يقبل التثبيت `admin/admin123` بصلاحية `super_admin`، ويُنشأ الحساب مع `must_change_password=0`.
- **Evidence:** `build/installer-smoke/installer-smoke.json`; `desktop.py:54-56`; `desktop.py:192-197`; `services/auth.py:83-92`.
- **Likely root cause:** ضبط `PHOENIX_DEFAULT_ADMIN_KEEP_PASSWORD=1` عمدًا، واختبار المثبّت يعتبر بقاء كلمة المرور شرط نجاح.
- **Release impact:** Release Blocker أمني حرج.
- **Recommended remediation:** إعادة تفعيل First Run Setup، أو توليد سر أولي فريد وعرضه مرة واحدة، ومنع شاشات الأعمال قبل تغييره.
- **Retest requirement:** تثبيت نظيف فعلي، رفض السر الافتراضي، وإثبات تغيير إلزامي وجلسة جديدة واختبارات صلاحيات.

## DEF-RC1-002 — أخطاء تقريب بمقدار سنت واحد في الحسابات المالية

- **Severity:** HIGH
- **Risk:** مبالغ ضريبية و`TTC` غير متطابقة في الفواتير والسجلات القانونية.
- **Page/Module:** Invoice Calculation / `services/billing.py` / `app.py`
- **Feature / Test Case:** HT, Retention, TVA, TTC — `FIN-001`, `FIN-002`, `INV-001`
- **Preconditions:** بيانات QA وقيم عشرية صالحة ومعدلات 5% و19%.
- **Steps to reproduce:** تشغيل 25 Golden/E2E scenarios و2,000 توليفة مولدة؛ مثال إجمالي بعد الاحتجاز `8,583,392.50`.
- **Expected result:** ضريبة `1,630,844.58` وتقريب عشري محدد ومتسق إلى خانتين.
- **Actual result:** التطبيق أعاد `1,630,844.57` و`TTC` أقل بسنت. فشلت 3/25 حالات E2E، و3/25 Golden Cases، وفشلت 473/2,000 توليفة مولدة؛ حُفظت تفاصيل أول 20 مخالفة.
- **Evidence:** `quality/evidence/critical_invoice_scenarios.json`; `quality/evidence/financial_oracles.json`; `services/billing.py:24-35`; `app.py:6323,6378`.
- **Likely root cause:** استخدام `float` وPython `round` على تمثيل ثنائي بدل حساب عشري ثابت الدقة.
- **Release impact:** Release Blocker مالي؛ بوابة Golden Tests فاشلة.
- **Recommended remediation:** اعتماد `Decimal` وسياسة تقريب موثقة، وتقييد دقة المدخلات، وإعادة حساب كل مستوى بالقاعدة المعتمدة.
- **Retest requirement:** 25+ Golden Cases، property tests، مطابقة DB/UI/PDF/XLSX، واختبار ترحيل القيم السابقة إن لزم.

## DEF-RC1-003 — نسب مالية صحيحة حسابيًا لكنها معنونة خطأ في PDF وExcel

- **Severity:** HIGH
- **Risk:** مستند قانوني يعرض معدلًا مختلفًا عن المعدل الذي حُسبت به القيمة.
- **Page/Module:** Invoice Documents / `app.py` / `services/invoice_exports.py`
- **Feature / Test Case:** Configurable Retention/TVA — `DOC-001`, `DOC-002`
- **Preconditions:** ضبط QA إلى Retention=10% وTVA=20% ثم إنشاء فاتورة جديدة.
- **Steps to reproduce:** إنشاء فاتورة HT=`85,000.00`؛ تصدير XLSX وPDF؛ مقارنة القيم والعناوين بالإعدادات.
- **Expected result:** إظهار `10%` و`20%` مع قيم `8,500.00` و`15,300.00`.
- **Actual result:** القيم حُسبت وفق 10%/20%، بينما بقيت العناوين `RETENUE DE GARANTIE 5%` و`TVA 19%` في الملفين.
- **Evidence:** `quality/evidence/rate_label_probe.json`; `quality/evidence/rate_label/rate_10_20.png`; `app.py:6026-6028`; `services/invoice_exports.py:74`.
- **Likely root cause:** نصوص نسب ثابتة منفصلة عن `app_settings`.
- **Release impact:** Release Blocker لدقة المستندات.
- **Recommended remediation:** توليد العناوين من معدل الفاتورة الفعلي وحفظ snapshot للمعدل إذا كان يتغير بعد الإصدار.
- **Retest requirement:** معدلات افتراضية وغير افتراضية عبر UI/DB/PDF/XLSX، ثم إعادة الفتح بعد تغيير الإعداد العام.

## DEF-RC1-004 — عمليات حذف تتغير حالتها عبر GET بلا CSRF

- **Severity:** HIGH
- **Risk:** حذف منطقي غير مقصود للفواتير وكيانات أعمال عبر رابط أو تحميل مسبق أو طلب مكرر.
- **Page/Module:** HTTP Routing / Delete Actions
- **Feature / Test Case:** Delete/Cancel/Idempotency — `SEC-003`, `INV-005`
- **Preconditions:** جلسة مستخدم مصادق له بصلاحية الحذف وبيان QA نشط.
- **Steps to reproduce:** إرسال `GET /invoices/delete?id=<QA_ID>` دون CSRF.
- **Expected result:** لا تغيير للحالة؛ قبول POST/DELETE فقط مع CSRF وتأكيد/سبب مناسب.
- **Actual result:** `deleted_at` تغير من NULL إلى timestamp. توجد مسارات GET مماثلة للفواتير وأوامر الشراء والمواقع والاتجاهات والأرشيفات.
- **Evidence:** `quality/evidence/http_repetition_continuation.json`; `app.py:664-757`; `app.py:1086-1120`.
- **Likely root cause:** ربط المعالجات المدمرة داخل `do_GET` بينما حماية CSRF مطبقة على POST فقط.
- **Release impact:** Release Blocker لخطر فقد/إخفاء بيانات الأعمال.
- **Recommended remediation:** تحويل التغييرات إلى POST/DELETE محمي بـCSRF، واستخدام lifecycle cancellation للفواتير.
- **Retest requirement:** جميع مسارات الحذف، direct-link/prefetch/double action، الأدوار، والاستعادة.

## DEF-RC1-005 — تعديل عنوان العميل لا يُحفظ

- **Severity:** HIGH
- **Risk:** بقاء بيانات قانونية/تجارية قديمة في السجلات والمستندات.
- **Page/Module:** Clients / `app.py`
- **Feature / Test Case:** Client Edit — `CLI-001`
- **Preconditions:** 10 عملاء QA محفوظون.
- **Steps to reproduce:** إرسال نموذج تعديل كل عميل مع عنوان مختلف؛ إعادة القراءة من قاعدة البيانات.
- **Expected result:** حفظ `adresse` الجديدة وإظهارها بعد إعادة الفتح.
- **Actual result:** فشلت 10/10 محاولات؛ بقي العنوان الأصلي، بينما نجحت دورات اتجاهات العملاء وأوامر الشراء والمواقع 10/10.
- **Evidence:** `quality/evidence/high_risk_crud_edits.json`; `app.py:3663-3685`.
- **Likely root cause:** جملة `UPDATE clients` لا تتضمن `adresse` رغم أن `INSERT` تتضمنه.
- **Release impact:** Release Blocker لسلامة بيانات العميل ودقة المستندات اللاحقة.
- **Recommended remediation:** إدراج العنوان في مسار التحديث مع اختبارات round-trip وaudit.
- **Retest requirement:** 10 تعديلات متنوعة، إعادة فتح، إعادة تشغيل، وتوليد مستند يستخدم العنوان المعدل.

## DEF-RC1-006 — تحميلات عامة بلا حدود حجم أو تحقق محتوى

- **Severity:** MEDIUM
- **Risk:** استهلاك ذاكرة/قرص محلي ورفع محتوى لا يطابق الامتداد.
- **Page/Module:** Multipart / Uploads
- **Feature / Test Case:** Logos and Attachments — `SEC-004`
- **Preconditions:** مستخدم مصادق له صلاحية الرفع.
- **Steps to reproduce:** مراجعة قارئ multipart ومسار `save_upload` ومقارنته بعبارات UI التي تعلن حدود 2/5 MB.
- **Expected result:** حد شامل لـ`Content-Length` وحد لكل ملف، وفحص نوع/توقيع المحتوى.
- **Actual result:** يُقرأ الطلب كاملًا إلى الذاكرة، والتحقق العام يقتصر على suffix. توجد حدود خاصة بواردات Excel فقط.
- **Evidence:** `app.py:1031-1065`; `services/uploads.py:5-19`; `app.py:3207,3894`.
- **Likely root cause:** غياب سياسة رفع مركزية.
- **Release impact:** مخاطرة متوسطة، مخففة جزئيًا بربط الخدمة على loopback والمصادقة.
- **Recommended remediation:** حدود مبكرة، فحص magic bytes، حذف آمن عند الفشل، و`nosniff`.
- **Retest requirement:** ملفات عند/فوق الحد، محتوى مزيف، أسماء Unicode، انقطاع، وتزامن.

## DEF-RC1-007 — رؤوس حماية HTTP الأساسية غير موجودة

- **Severity:** MEDIUM
- **Risk:** تقليل دفاعات المتصفح ضد framing/content sniffing وتسرب referrer/cache.
- **Page/Module:** HTTP Responses
- **Feature / Test Case:** Security Headers — `SEC-004`
- **Preconditions:** جلسة مصادق عليها.
- **Steps to reproduce:** طلب `/` وفحص headers.
- **Expected result:** `X-Content-Type-Options`, frame protection, referrer/cache policy وCSP ملائمة.
- **Actual result:** كل الحقول المقاسة غائبة.
- **Evidence:** `quality/evidence/runtime_probe.json`; `app.py:1122-1132`.
- **Likely root cause:** `respond()` لا يضيف headers مركزية.
- **Release impact:** مخاطرة متوسطة؛ loopback يقلل التعرض ولا يلغيه.
- **Recommended remediation:** إضافة headers مركزية وتكييف inline scripts قبل CSP صارمة.
- **Retest requirement:** جميع أنواع الاستجابة وملفات uploads/exports وصفحات login/authenticated.

## DEF-RC1-008 — حقل كلمة المرور يفتقد autocomplete مناسبًا

- **Severity:** LOW
- **Risk:** تجربة أقل احترافية مع مديري كلمات المرور.
- **Page/Module:** Login
- **Feature / Test Case:** Accessibility / Password Manager — `A11Y-001`
- **Preconditions:** صفحة Login فعلية.
- **Steps to reproduce:** فتح الصفحة وفحص console/DOM.
- **Expected result:** `autocomplete="current-password"` واسم مستخدم مناسب.
- **Actual result:** Chromium أصدر تحذيرًا بأن حقل كلمة المرور يفتقد autocomplete.
- **Evidence:** `.playwright-cli/console-2026-08-25T13-58-16-852Z.log`.
- **Likely root cause:** helper النموذج لا يحدد خصائص autocomplete.
- **Release impact:** لا يحجب الإصدار منفردًا.
- **Recommended remediation:** إضافة autocomplete واختبار قارئ الشاشة/مدير كلمات المرور.
- **Retest requirement:** Chrome/WebView2 والتنقل بلوحة المفاتيح.
