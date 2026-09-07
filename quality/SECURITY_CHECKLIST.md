# قائمة المراجعة الأمنية العملية — RC-1

النطاق: تطبيق Windows محلي، خادم HTTP loopback، Python/JavaScript/SQLite، الملفات والبناء والترخيص. ليست Penetration Test ولا شهادة أمن كاملة.

| المجال | الحالة | الدليل / الملاحظة |
|---|---|---|
| أسرار الإصدار | PASS | المفتاح الخاص موجود محليًا لكنه ignored؛ فحص الحزمة: 0 private keys و1 public key |
| Bind | PASS | Desktop يستخدم loopback وrandom port؛ smoke runtime على `127.0.0.1` |
| First-run authentication | FAIL | `admin/admin123`, `must_change_password=0` — `DEF-RC1-001` |
| Password hashing/lockout | PASS | PBKDF2-SHA256/200k، lockout 5 محاولات/15 دقيقة، اختبارات auth PASS |
| Sessions/cookies | PASS مع قيد | server-side token، HttpOnly، SameSite=Lax؛ عدم Secure مبرر بـHTTP loopback |
| CSRF/HTTP methods | FAIL | POST CSRF يمنع الاستمرار، لكن destructive GET يغير الحالة — `DEF-RC1-004` |
| Authorization | PASS للنطاق المختبر | lifecycle/viewer/permission override/admin tests PASS؛ لم تُنفذ مصفوفة كل route×role يدويًا |
| Security headers | FAIL | CSP/nosniff/frame/referrer/cache غائبة — `DEF-RC1-007` |
| SQL injection | PASS بالمراجعة العملية | القيم Parameterized؛ identifiers الديناميكية المكتشفة محصورة بقوائم ثابتة |
| XSS | PASS بالمراجعة العملية | helper `h()` وJSON/DOM patterns؛ لم تُجرَ حملة fuzz كاملة |
| Path traversal | PASS | resolve + containment لمسارات uploads/exports/backups؛ أسماء upload عشوائية |
| Upload validation | FAIL | suffix فقط ولا حد عام للصور/المرفقات — `DEF-RC1-006` |
| Request DoS | FAIL | `Content-Length` يُقرأ كاملًا دون حد عام؛ loopback/auth يقللان التعرض |
| Subprocess injection | PASS بالمراجعة | PDF renderer يستقبل URL/path مشتقين داخليًا كقائمة args، لا shell interpolation للمستخدم |
| Backup restore | PASS | SQLite header/integrity والتحقق بعد restore وpre-restore backup؛ round-trip فعلي ناجح |
| Logging | PASS بالمراجعة المحدودة | access log لا يسجل body/password/session token؛ لم يُفحص log retention قانونيًا |
| Licence security | PASS | Ed25519/machine binding/tamper/wrong-machine/vault tests PASS |
| Dependency audit | PASS جزئي | `npm audit`: 0 vulnerabilities؛ `pip check`: no broken requirements؛ `pip-audit` غير مثبت |
| Build key separation | PASS | `quality/evidence/build_key_scan.json`: private=0/public=1 |
| Artifact integrity | PASS | SHA256SUMS طابق ZIP/Publisher/Installer جميعًا |

القيود: لم يُستخدم scanner ديناميكي خارجي، ولم يُنفذ اختبار شبكة بعيدة لأن التصميم loopback، ولم تُجرَ اختبارات ضغط خصمية كبيرة على الرفع كي لا تُهدد استقرار المضيف.
