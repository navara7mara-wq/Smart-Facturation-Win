# بيان بناء RC-2 المكتمل

## تعريف الإصدار

- Application version: `2.5.2`.
- Database schema: `19`؛ migration الجديدة: `invoice_financial_rate_snapshots`.
- RC-1 audit checkpoint: `c79f42d64f3a0b07c93e919a56c2f5bdddf091f2` / `refs/tags/rc-1-audited`.
- RC-2 remediation checkpoint السابق: `3ac851264185873b13d9e2352901ca43810a1de3` / `refs/tags/rc-2-remediation`.
- مرجع الإكمال غير القابل للالتباس: `refs/tags/rc-2-completed-remediation`؛ يسجل annotated tag نفسه commit/tree/parent النهائيين.

## ملفات البناء النهائية

| الملف | SHA-256 |
|---|---|
| `dist/PhoEniX_BPU_Portable_2.5.2.zip` | `c6dface3a85228477300c9819f5f96640148268a0b806d3b314ab816f96be910` |
| `dist/installer/PhoEniX_BPU_Setup_2.5.2.exe` | `b6404b3f12264ec290e620140eb953376db9503466bb0afd0e3b0ec080efe564` |
| `dist/publisher/PhoEniX License Studio.exe` | `b3a3e013dfbda483f31b073dbdda6b95c2b98e2adbaedf2b0b72153a3668a1cf` |

القيم مطابقة 3/3 مع `dist/SHA256SUMS.txt` المحفوظ في `quality/remediation/evidence/build_rc2/SHA256SUMS.txt`.

## نتائج البناء والتوزيع

- PyInstaller Desktop: PASS.
- Publisher tool: PASS؛ private signing key غير مضمّن.
- Portable ZIP: PASS.
- Inno Setup production installer: PASS.
- Clean installer smoke + reinstall + uninstall + user-data preservation: PASS.
- Real installer upgrade2.5.1→2.5.2: PASS.
- Authenticode: غير منفذ لعدم توفير شهادة ناشر معتمدة؛ SHA-256 manifest لا يعوض توقيع الناشر.

ملفات `dist/` لا تُعد بديلًا عن المرجع المصدر Git؛ المرجع `rc-2-completed-remediation` هو checkpoint المعالجة المصدرية.
