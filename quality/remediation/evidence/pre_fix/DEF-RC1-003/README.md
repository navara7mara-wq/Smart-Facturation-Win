# DEF-RC1-003 — أدلة تاريخية قبل الإصلاح

هذه الحزمة **PRE-FIX / FAIL** فقط. أنشأها اختبار RC-1 بتاريخ 2026-08-25 لإثبات العيب: استُخدمت نسب `RG=10%` و`TVA=20%` في الحسابات، بينما بقيت عناوين PDF/XLSX عند `5%` و`19%`.

لا يجوز استخدام أي ملف هنا كدليل PASS أو كدليل على سلوك RC-2 الحالي.

## أصل الملفات

- `rc1_audit/`: النسخة التي كانت تحت `quality/evidence/rate_label/` مع نتيجة المجس التاريخية.
- `rc2_remediation_copy/`: النسخة المكررة التي كانت تحت `quality/remediation/evidence/rate_label/`.
- حُفظت الملفات ولم تُحذف، وأعيدت تسميتها بالبادئة `PRE_FIX_FAIL_` لإزالة أي التباس.
- المسارات الأصلية لا تزال متاحة بصورة immutable ضمن المرجع التاريخي `rc-1-audited`.

## دليل RC-2 المرجعي بعد الإصلاح

- عشرة سيناريوهات نهائية: `quality/remediation/evidence/financial_rc2/e2e_10/`.
- مطابقة مستقلة بين UI وDB وPDF وXLSX: `quality/remediation/evidence/POST_FIX_DEF-RC1-003_RECONCILIATION.json`.
- النتيجة المرجعية: **10/10 PASS**، وتتضمن الفواتير 06–10 بنسب `RG=10%` و`TVA=20%` في العناوين والقيم.

