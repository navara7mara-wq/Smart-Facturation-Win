# أدلة الاختبار النهائية — RC-2 Completed Remediation

تاريخ التنفيذ النهائي: 2026-08-26. جميع الاختبارات المدمرة والإنشائية استخدمت قواعد بيانات ومجلدات QA معزولة.

## النتائج الآلية

- Targeted financial/migration/fresh-install: **54/54 PASS** في 5.78s.
- Full strict regression: **136/136 PASS** في 129.38s مع `-W error` و`basetemp` معزول.
- Compile check: **PASS** لـ`app.py`, `db.py`, `desktop.py`, `database/`, `services/`, `scripts/`, `tests/` وscripts المعالجة.
- Visual strict: **16/16 PASS**؛ أدنى تشابه 99.800%.

## البوابة المالية

- Golden: **25/25 PASS**.
- Generated/property: **2,500/2,500 PASS** باستخدام seed ثابت `20260825` وأوراكل مستقل مبني على `Decimal` و`ROUND_DOWN` باتجاه الصفر.
- الحالات `FIN-G-06`, `FIN-G-07`, `FIN-G-19`: **3/3 PASS** وفق القرار المعتمد، لا وفق أوراكل RC-1 المؤقت.
- truncation الصريح: `12.349→12.34`, `12.345→12.34`, `12.999→12.99`, `100.005→100.00`, `-12.999→-12.99`: **PASS**.
- TVA على `HT après RG`: **PASS** في Golden وproperty وE2E.

## عشر فواتير E2E

- 10/10 create → save → reopen → edit → save → restart → reopen → DB → preview → PDF → XLSX.
- الفواتير 1–5 أخذت `RG=5%/TVA=19%`، والفواتير 6–10 أخذت `RG=10%/TVA=20%`.
- تغيير defaults لاحقًا إلى 7/21 لم يغيّر أي snapshot قائم.
- UI/DB/PDF/XLSX: HT وRG وHT après RG وTVA وTTC والعناوين متطابقة 10/10.
- ملفات الإثبات: `quality/remediation/evidence/financial_rc2/e2e_10/`، عشرة PDF وعشرة XLSX.
- هذه المجموعة هي الدليل المرجعي الوحيد بعد الإصلاح لـ`DEF-RC1-003`؛ نتيجة المطابقة المستقلة UI/DB/PDF/XLSX هي **10/10 PASS** في `quality/remediation/evidence/POST_FIX_DEF-RC1-003_RECONCILIATION.json`.
- ملفات `rate_10_20.*` القديمة ثبت أنها PRE-FIX FAIL، وحُفظت باسم صريح تحت `quality/remediation/evidence/pre_fix/DEF-RC1-003/`؛ ليست أدلة PASS حالية.
- كشف فحص Open XML توسعًا ثنائيًا سابقًا `78.40000000000001`؛ صُحح المصدر وأصبحت literals الرسمية `12.34`, `78.40`، مع regression يمنع الرجوع.

## Migration تاريخية معزولة

- المصدر: قاعدة الأعمال v17 فُتحت read-only، ثم نُسخت إلى `tmp/rc2-financial/`.
- migrations المطبقة على النسخة: `[18, 19]`.
- الفواتير: 5 قبل و5 بعد.
- كل القيم المالية والأسطر وعلاقات invoice-sites بقيت دون تغيير.
- backfill: 5/5 فواتير `rg_rate=5`, `tva_rate=19`.
- بعد تغيير defaults في النسخة إلى10/20 بقيت كل الفواتير التاريخية5/19 بعد restart.
- 5 XLSX و2 PDF تاريخية تمثيلية: labels والقيم 5/19 مطابقة، مع فحص نصي وبصري.
- integrity=`ok`، FK=0، duplicates=0، orphan lines=0.
- الدليل التفصيلي: `quality/remediation/evidence/financial_rc2/historical_migration.json`.

## Fresh Install وUpgrade

- Fresh install/update/uninstall: **PASS**؛ قاعدة فارغة، schema19، First Run آمن، HTTP/XLSX/PDF/WebView PASS، بيانات المستخدم محفوظة.
- Upgrade الحقيقي 2.5.1→2.5.2: **PASS**؛ invoice marker وقيمه وسطره وعلاقاته محفوظة، schema17→19، snapshot5/19، integrity=`ok`, FK=0، جلسة المدير القديمة أُبطلت ودوره محفوظ.
- الأدلة: `quality/remediation/evidence/build_rc2/installer-smoke.json` و`upgrade-smoke.json`.

## حماية البيانات التاريخية

- `data/pos_ai.sqlite3` SHA-256 قبل وبعد: `7733604AA540A6FFEE052CD9CD3E6CBACF7B1805BBC237B87900699805C4F601` — مطابق.
- `quality/DEFECT_REGISTER.md` SHA-256: `40924A1DA3FE297325CA940AA998864F6C375D8647295DA40301AE6B7FEA2EB3` — لم يُعدل.
- لم تُحذف أدلة RC-1 أو migration backups أو backup evidence.

## النتيجة

Financial Regression Gate: **PASS**. Security Regression Gate: **PASS**. Full Regression Gate: **PASS**. Build Gate: **PASS**.
