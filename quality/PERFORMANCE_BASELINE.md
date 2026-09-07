# خط أساس الأداء — RC-1

القياسات wall-clock على جهاز التدقيق الحالي، وليست SLA تعاقدية. قاعدة QA النهائية ضمت 610 عنصر BPU و1,132 تعيين ST و60 أمر شراء و60 موقعًا و49 فاتورة، بينما اختبر الاختبار الآلي تجميع Dashboard لـ2,000 فاتورة.

| العملية | البيانات | التكرارات | Median | P95 / Max | النتيجة |
|---|---|---:|---:|---:|---|
| أول تشغيل مثبت نظيف | مجلد مستخدم فارغ | 1 | 8,152 ms | 8,152 ms | PASS تقنيًا |
| تشغيل بعد update | بيانات محفوظة | 1 | 8,746 ms | 8,746 ms | PASS |
| GET Login | QA | 1 | 7.49 ms | 7.49 ms | PASS |
| POST Login | QA | 1 | 94.10 ms | 94.10 ms | PASS |
| Dashboard | QA | 10 | 17.54 ms | 31.57 / 31.79 ms | PASS |
| Table Facturation | QA | 10 | 31.85 ms | 42.52 / 45.89 ms | PASS |
| Invoices form | QA | 10 | 14.69 ms | 33.61 / 33.96 ms | PASS |
| Invoice search request | QA | 10 | 15.49 ms | 32.49 / 34.60 ms | زمن PASS؛ صحة البحث اختُبرت منفصلًا 10/10 |
| Clients | QA | 10 | 19.59 ms | 32.28 / 32.43 ms | PASS |
| BPU | 610 records | 10 | 13.36 ms | 30.91 / 31.62 ms | PASS |
| Settings | QA | 10 | 18.68 ms | 31.75 / 32.07 ms | PASS |
| Invoice XLSX | عينة فعلية | 1 | 2,780.92 ms | 2,780.92 ms | Baseline فقط |
| Invoice PDF | عينة فعلية | 1 | 4,877.44 ms | 4,877.44 ms | Baseline فقط |
| Backup/Restore | DB بحجم نحو 672 KB | 1 | لم يُفصل زمنيًا | أقل من مهلة 120s | PASS وظيفيًا |
| Dashboard aggregation | 2,000 invoices | 1 automated | غير مقاس منفصلًا | اكتمل ضمن suite | PASS وظيفيًا |

## الموارد

- قبل 70 طلب صفحة وتوليد XLSX/PDF: Working Set = `128,045,056` bytes.
- بعد القياس: `127,528,960` bytes؛ delta = `-516,096` bytes.
- لم يظهر نمو ذاكرة واضح أو hanging process في هذه النافذة المحدودة.
- لم تُنفذ جلسة soak طويلة لساعات، ولذلك لا يُستنتج غياب كل memory/resource leak.

الدليل: `quality/evidence/runtime_probe.json`, `build/installer-smoke/installer-smoke.json`.
