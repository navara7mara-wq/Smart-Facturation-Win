SAPTA Dashboard V6

التعديلات:
1) تحويل كل العرض المالي داخل Dashboard إلى KDA (كيلو دينار):
   - Total TTC
   - محور وقيم Montant TTC par mois
   - Montant TTC في Factures non déposées
   - Montant TTC في Dernières factures
   القيم الأصلية في قاعدة البيانات لا تتغير؛ العرض فقط مقسوم على 1000.
2) تثبيت Footer حقوق النشر قرب أسفل الشاشة مع فراغ جمالي 8px أسفله.
3) الإبقاء على ارتفاع Footer المصغر 36.5px.

استبدل:
app.py
static/style.css
ثم شغل python app.py واضغط Ctrl+F5.
