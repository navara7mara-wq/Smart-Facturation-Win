# تحقق نظافة أدلة RC-2

تاريخ التحقق: 2026-08-26. هذا تحقق نظافة أدلة قبل Independent Final Certification، وليس شهادة إنتاج.

## حسم الالتباس

ثبت من `rate_label_probe.json` ومن فحص محتوى PDF وXLSX أن ملفات `rate_10_20.*` القديمة هي أدلة **PRE-FIX FAIL** لـ`DEF-RC1-003`: القيم محسوبة بـ10/20 بينما العناوين 5/19. نُقلت النسختان التاريخيتان إلى `pre_fix/DEF-RC1-003/`، وأعيدت تسميتهما بالبادئة `PRE_FIX_FAIL_`، وأزيلت المجلدات القديمة الملتبسة.

## دليل POST-FIX المرجعي

- المسار: `financial_rc2/e2e_10/`.
- المحتوى: 10 PDF و10 XLSX نهائية، بلا مجلدات generated مكررة.
- المطابقة الآلية المستقلة: **10/10 PASS** عبر UI وDB وPDF وXLSX.
- الفواتير 01–05 تعرض وتستخدم 5/19؛ الفواتير 06–10 تعرض وتستخدم 10/20.
- الفحص البصري التمثيلي للفاتورة 06 أكد عناوين `RETENUE DE GARANTIE 10%` و`TVA 20%` وقيم `78.40 / 7.84 / 70.56 / 14.11 / 84.67` في PDF وXLSX.
- نتيجة المطابقة التفصيلية: `POST_FIX_DEF-RC1-003_RECONCILIATION.json`.

## سلامة المرجع التاريخي

- `rc-1-audited`: لم يُنقل أو يُستبدل.
- `rc-2-remediation`: لم يُنقل أو يُستبدل.
- `rc-2-completed-remediation`: لم يُنقل أو يُستبدل؛ بقي مرجعًا تاريخيًا للحالة السابقة للتنظيف.
- لأن تنظيف الأدلة غيّر شجرة evidence، يجب إنشاء annotated tag جديد بدل تعديل أي وسم تاريخي.

تفاصيل `refs/tags/rc-2-completed-remediation` قبل التنظيف:

- annotated tag object: `76d7992e87fe056c7f74a4258e6a462044baaa2c`
- commit: `6d3d9b48cbf6b8631e7397de5424e10b4759fbf3`
- tree: `19bd86bcfa90bcd352377c85ccc0c7ebf439e890`
- parent: `3ac851264185873b13d9e2352901ca43810a1de3`

## حدود المحتوى المقبول

المسموح داخل checkpoint: source/tests/QA documentation، أدلة PDF/XLSX/PNG/JSON المقصودة، وملفات البناء الموثقة. غير المسموح: قاعدة الأعمال، قواعد QA، النسخ الاحتياطية، مفاتيح خاصة، أسرار إنتاج، سجلات، caches وملفات temp.

فحص الشجرة المرشحة شمل 259 مسارًا: صفر قاعدة بيانات، صفر backup، صفر ملف temp/cache/log، صفر `.env`، صفر private-key marker، وصفر مسار evidence قديم ملتبس. توجد كلمات مرور ثابتة فقط كـQA fixtures صريحة داخل scripts/tests المعزولة؛ لا توجد credentials إنتاجية أو مفاتيح خاصة ضمن checkpoint.

ظل SHA-256 لقاعدة الأعمال `data/pos_ai.sqlite3` مساويًا لـ`7733604AA540A6FFEE052CD9CD3E6CBACF7B1805BBC237B87900699805C4F601`، وظل SHA-256 لسجل العيوب التاريخي مساويًا لـ`40924A1DA3FE297325CA940AA998864F6C375D8647295DA40301AE6B7FEA2EB3`.
