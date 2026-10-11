# Semantic SEO Tool 2.0

> نسخة Production-ready تجمع تحليل AI مع تحقق دليل حتمي، لوحة جودة، حماية عمليات أقوى، وفحوص جاهزية وتشغيل واضحة.

## الجديد في 2.0

- درجة دليل لكل كيان وثقة لكل فجوة، محسوبة من النصوص المستخرجة فعليًا.
- لوحة Quality Control داخل الواجهة وورقة Quality في Excel.
- Preflight حي لعدد المصادر وحجم الملفات وكثافة العمل والحدود.
- حالة الخادم، Request ID، Server Timing، `/ready` و`/api/meta`.
- إلغاء آمن باستخدام `cancel_token` مستقل لكل عملية وحد أقصى للمهام المتزامنة.
- Same-Origin enforcement وHSTS عند HTTPS ودعم ProxyFix وRedis للـrate limiting.
- تصدير JSON منقّى من الرموز المؤقتة، وحفظ الإعدادات غير الحساسة فقط.
- Watchdog لاتصال SSE يلغي المهمة على الخادم عند توقف التحديث طويلًا.

راجع [UPGRADE_PLAN_AR.md](UPGRADE_PLAN_AR.md) للخطة الشاملة المنفذة وخارطة الطريق، و[SECURITY.md](SECURITY.md) لنموذج الأمان وإعداد الإنتاج.

أداة عربية/إنجليزية لتحليل الكيانات الدلالية، مقارنة المحتوى بالمنافسين، اكتشاف الفجوات، وإنشاء تحليل SERP وهيكلة محتوى احترافية مبنية على الأدلة الفعلية المستخرجة من صفحات المنافسين.

## ما الذي تفعله الأداة؟

### التحليل الدلالي

- قراءة رابط أو ملف للمحتوى الأساسي.
- قراءة حتى 30 مصدرًا منافسًا من روابط أو ملفات.
- تقسيم المحتوى الطويل إلى أجزاء مترابطة من دون قصّ 15 ألف حرف أو تجاهل نهاية الصفحة.
- استخراج الكيانات والعناقيد والمرادفات والأهمية والبروز وعدد الذكر، ثم إسناد درجة دليل حتمية لكل كيان.
- حساب الفجوات محليًا بصورة قابلة للتفسير، مع تحقق AI اختياري ودرجة ثقة مستقلة لكل فجوة.
- إنشاء Content Brief بصيغة HTML دلالية حقيقية.
- حفظ الأدلة النصية مؤقتًا على الخادم وتحميلها عند الطلب بدل إرسال النصوص الكاملة إلى المتصفح.
- تصدير Excel محمي من Formula Injection.

### تحليل SERP

يمر تحليل SERP بالمراحل التالية:

1. استخراج الصفحة كاملة قدر الإمكان من كل منافس.
2. مسح محلي كامل لكل حرف وكلمة وعنوان وفقرة، بحيث لا يختفي مصدر بسبب طول Prompt أو Rate Limit.
3. إنشاء Evidence Pack موزع من بداية الصفحة ووسطها ونهايتها، ثم إثراء عدة منافسين في طلب AI واحد بدلاً من عشرات طلبات الأجزاء.
4. دمج العناوين والموضوعات والكيانات والأسئلة وإشارات الثقة والعناصر التجارية من جميع المصادر.
5. إنشاء جدول منافسين برمجي بصف إلزامي لكل رابط CSV؛ المصدر المتعذر يظهر كـ«أدلة محدودة» ولا يُحذف بصمت.
6. بناء قائمة تغطية عامة وقائمة خاصة بكل منافس قبل إنشاء الهيكلة.
7. إنشاء هيكلة نهائية مرتبة حسب رحلة القارئ، لا مجرد رصّ عناوين.
8. فحص آلي للهيكلة والتغطية، وتشغيل إصلاح AI إضافي فقط إذا كانت بنية العناوين نفسها ناقصة بشدة.
9. ضمان برمجي بعد التوليد: اختيار نقطة تغطية مميزة من كل منافس ودمج أي نقطة مفقودة داخل أقرب قسم منطقي، لذلك لا يختفي المنافسون المتأخرون من الهيكلة.

الناتج موحّد بصيغة HTML دلالية آمنة: `h1`, `h2`, `h3`, `h4`, `p`, قوائم وجداول. لا تظهر رموز Markdown مثل `#` أو `**` في الواجهة أو النسخ أو ملف Word.

## استخراج المحتوى من المواقع

تستخدم الأداة أكثر من استراتيجية وتدمج النتائج الفريدة بدل الاعتماد على قارئ واحد:

- طلب HTTP مباشر بترويسات متصفح وCloudflare-friendly scraper.
- HTML الدلالي وبيانات SEO والصور والعناوين والقوائم والجداول.
- JSON-LD وبيانات hydration في Next.js وNuxt وWix.
- حالات بيانات شائعة في متاجر Salla وZid وواجهات SPA.
- WordPress REST API عندما تعرض الصفحة endpoint صالحًا على النطاق نفسه.
- Jina Reader كخيار احتياطي يمكن تعطيله للروابط الحساسة.
- Chromium عبر Playwright للصفحات المعتمدة على JavaScript.
- تمرير الصفحة لتحفيز lazy loading، وفتح FAQ وaccordion وtabs وread-more.
- قراءة النص الظاهر وOpen Shadow DOM وsame-origin iframes.

لا يمكن لأي أداة ضمان قراءة محتوى خلف تسجيل دخول، CAPTCHA، paywall، حماية bot صارمة، أو محتوى تمنعه صلاحيات المتصفح. في هذه الحالات يمكن رفع المحتوى كملف بدل الرابط.

## السرعة وحماية Nara API من 429

المسار السريع الجديد يقلل عدد طلبات SERP جذريًا. مثال 30 منافسًا يُعالج عادة في نحو 4 دفعات Profile متكيفة الحجم، ثم طلب نهائي واحد يجمع الاستراتيجية والهيكلة، بدل طلب لكل جزء من كل صفحة. الإصلاح الإضافي لا يعمل إلا إذا خرجت بنية العناوين ناقصة بشدة.

يوجد منظم مركزي لكل طلبات Nara داخل العملية:

- طلبان متزامنان كحد آمن افتراضي مع فاصل dispatch قصير.
- Shared cooldown: عند أول 429 تتوقف الطلبات المنتظرة كلها بدل استمرار الضغط.
- احترام `Retry-After`، وإلا exponential backoff مع jitter.
- خفض عدد محاولات 429 الطويلة لأن الوقاية أصبحت في التجميع والمعدل، لا الانتظار المتكرر.
- fallback محلي كامل: إذا فشلت دفعة AI تبقى كل المصادر ونتائج المسح المحلي موجودة.
- كاش ذاكرة محدود ومنتهي الصلاحية يمنع تنزيل الرابط نفسه مرتين عند الانتقال من المعاينة إلى التحليل.

الإعدادات المتوازنة الموصى بها:

```env
NARA_MAX_CONCURRENT_REQUESTS=2
NARA_MIN_REQUEST_INTERVAL=0.75
NARA_429_MAX_RETRIES=4
NARA_429_MAX_BACKOFF_SECONDS=24
SERP_PROFILE_BATCH_SIZE=8
SERP_PROFILE_BATCH_WORKERS=2
SERP_PROFILE_BATCH_MAX_CHARS=76000
```

إذا كان حساب Nara منخفض الحصة ويظهر 429، استخدم `NARA_MAX_CONCURRENT_REQUESTS=1` و`NARA_MIN_REQUEST_INTERVAL=1.5` أولاً، بدل القفز مباشرة إلى 4–5 ثوانٍ.

## تشغيل محلي على Windows

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
python app.py
```

ثم افتح:

```text
http://127.0.0.1:5000
```

الرسالة التالية طبيعية عند التشغيل المحلي وليست سبب خطأ SERP:

```text
WARNING: This is a development server.
```

للتشغيل المحلي بخادم إنتاج على Windows:

```powershell
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

## تشغيل Linux/macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
python app.py
```

تشغيل إنتاجي:

```bash
gunicorn app:app --worker-class gthread --workers 1 --threads 4 --timeout 900
```

## Docker

Docker يثبت Chromium واعتمادياته تلقائيًا:

```bash
docker build -t semantic-seo-tool .
docker run --rm -p 5000:5000 --env-file .env semantic-seo-tool
```

## Render

ملف `render.yaml` يثبت Playwright Chromium ويستخدم Worker واحدًا و4 threads مع timeout مناسب. الإعدادات الحالية تستخدم 8 اتصالات استخراج خفيفة، ومتصفحين كحد أقصى، وطلبَي AI متزامنين. على خطة قليلة الذاكرة يمكن خفض `BROWSER_MAX_CONCURRENT` إلى `1` دون التأثير على بقية المسار.

## أهم الإعدادات

```env
NARA_API_KEY=
NARA_API_URL=https://router.bynara.id/v1/chat/completions
NARA_MODEL=mistral-large

MAX_COMPETITORS=30
MAX_CHARS=2000000
MAX_FILE_BYTES=15728640
MAX_URL_RESPONSE_BYTES=15728640

NARA_CHUNK_CHAR_LIMIT=9000
SERP_CHUNK_CHAR_LIMIT=18000
SERP_PROFILE_BATCH_SIZE=8
SERP_PROFILE_BATCH_WORKERS=2
SERP_PROFILE_BATCH_MAX_CHARS=76000
SERP_PROFILE_MAX_TOKENS=4200
SERP_PROFILE_EVIDENCE_CHAR_BUDGET=12000
SERP_AUDIT_MAX_TOKENS=4200
SERP_FINAL_MAX_TOKENS=8000
CONTENT_BRIEF_MAX_TOKENS=7000

ENABLE_JINA_FALLBACK=true
ENABLE_BROWSER_FALLBACK=true
ENABLE_URL_MEMORY_CACHE=true
URL_MEMORY_CACHE_TTL_SECONDS=600
URL_MEMORY_CACHE_MAX_CHARS=12000000
BROWSER_RENDER_TIMEOUT=15
BROWSER_NETWORK_IDLE_TIMEOUT=5
BROWSER_MAX_CONCURRENT=2
MIN_ACCEPTABLE_EXTRACTED_CHARS=1200
STRONG_EXTRACTED_CHARS=4500
STRONG_EXTRACTED_HEADINGS=3

TEMP_FILE_TTL_SECONDS=7200
```

`MAX_CHARS` حد أمان لكل نص مستخرج. القيمة الافتراضية مليونا حرف، ويمكن رفعها مع مراعاة الذاكرة وتكلفة التحليل.

## الخصوصية والأمان

- مفتاح المستخدم يبقى في `sessionStorage` داخل التبويب الحالي.
- عنوان Nara API يتحكم فيه الخادم، ولا يمكن للمتصفح تحويل المفتاح إلى endpoint مهاجم.
- محتوى المحرر يُسترجع بطلب `POST` باستخدام token عشوائي قصير العمر، مع `Cache-Control: no-store`، ولا يشارك مسار أدلة المنافسين.
- حماية SSRF على الرابط وكل redirect وطلبات Chromium الفرعية.
- منع عناوين الشبكات الداخلية والمنافذ غير المسموحة.
- حدود للملفات والاستجابات وOffice zip bombs.
- HTML الناتج يُنظف ويُعرض من خلال allowlist آمنة.
- ملفات Word تستخدم عناوين Word أصلية Heading 1–4 وفقرات وقوائم وجداول وRTL.
- تقارير Excel محمية من Formula Injection.
- ملفات التقارير والأدلة مؤقتة وتنتهي افتراضيًا بعد ساعتين.
- Rate limiting للمسارات الحساسة.

تنبيه خصوصية: Jina Reader خدمة خارجية وتستقبل الرابط العام المراد قراءته. عطّله عند التعامل مع روابط سرية:

```env
ENABLE_JINA_FALLBACK=false
```

## تجربة الاستخدام

- واجهة متجاوبة عربية/إنجليزية وRTL/LTR.
- Dark mode.
- رسوم انتقال وحالات تحميل ومراحل تقدم ونسبة مئوية.
- سحب وإفلات للملفات.
- إلغاء تعاوني للتحليل على الخادم.
- رسائل خطأ منفصلة للتحليل الدلالي وSERP.
- جدول محتويات تلقائي للتقرير.
- نسخ Rich HTML يحافظ على العناوين والقوائم والجداول في Word وGoogle Docs.
- تنقل بلوحة المفاتيح، `Escape` لإغلاق النوافذ، و`prefers-reduced-motion` لذوي الحساسية للحركة.

### المحرر الحي

- يصبح المحرر مساحة العمل الأساسية فور اكتمال تحليل الكيانات، مع زر واضح للانتقال إلى التقرير والعودة.
- يُحمَّل محتوى المستخدم المستخرج تلقائيًا من مخزن مؤقت منفصل عن أدلة المنافسين، دون وضع النص الكامل داخل استجابة SSE.
- تقييم لحظي شفاف للجاهزية الدلالية: الفجوات المهمة، الكيانات الأساسية، جاهزية التحرير، وخصم التكرار الزائد.
- اقتراحات مرتبة حسب الأولوية بجانب النص، وتعرض الكيان والسبب ومكان الإدراج المقترح داخل المحتوى.
- زر «تطبيق التعديل» يضيف مسودة تحريرية آمنة في المكان المقترح ويحددها مباشرة لتعديلها، مع تراجع واستعادة الأصل.
- بحث وفلاتر للاقتراحات: الناقص، المغطى، تحذيرات التكرار، والكل.
- حفظ تلقائي داخل `sessionStorage` للتبويب الحالي فقط، مع نسخ وتنزيل TXT واختصار `Ctrl/⌘ + S`.
- لا تُرسل تعديلات المحرر إلى الذكاء الاصطناعي تلقائيًا؛ التقييم والاقتراحات بعد التحليل تعمل محليًا في المتصفح.

## الاختبارات

```bash
pip install -r requirements-dev.txt
pytest -q
```

تنجح حاليًا 30 حالة اختبار، مع اختبار تكامل Flask مشروط بتوفر حزمة Flask في بيئة الاختبار. تشمل الاختبارات:

- استخراج DOCX وXLSX.
- حالات hydration لمتاجر وواجهات SPA.
- الحفاظ على النص كاملًا أثناء التقسيم.
- التطبيع العربي وحساب الفجوات.
- حماية SSRF وExcel Formula Injection.
- تنظيف HTML وتحويل Markdown القديم كـfallback فقط.
- Word بعناوين أصلية.
- فحص جودة هيكلة SERP.
- ضمان ظهور كل منافس في جدول المراجعة وتأثير كل مصدر في التغطية النهائية.
- كاش الروابط ومنع تكرار التنزيل، والمسار التكيفي الذي يتخطى Jina/Chromium عند كفاية HTML المباشر.
- عقد واجهة المحرر، عدم تكرار IDs، واكتمال مفاتيح الترجمة العربية والإنجليزية.

## هيكل المشروع

```text
app.py                    نقطة التشغيل
config.py                 الإعدادات والحدود
routes/analyze.py         preview/analyze/SERP/download/evidence/editor-content/cancel
services/extraction.py    قراءة الملفات والروابط والمتصفح وCMS APIs
services/nara.py          Nara، pacing، cooldown، التقسيم، الدمج
services/semantic.py      التطبيع والفجوات الدلالية
services/quality.py       درجات الدليل والثقة ولوحة الجودة
services/verification.py  التحقق المقيد من الفجوات
services/content_brief.py Content Brief بصيغة HTML دلالية
services/serp.py          Full-scan + batched AI + تغطية كل المنافسين وفحص الجودة
services/rich_content.py  تنظيف وتوحيد HTML
services/docx_export.py   Word أصلي H1–H4 وقوائم وجداول وRTL
services/excel.py         تقرير Excel
services/temp_store.py    تخزين مؤقت منتهي الصلاحية
utils/security.py         SSRF وحماية الخلايا
static/                   الواجهة والحركات والترجمات
tests/                    اختبارات الرجوع
```

## بوابة التحقق قبل الإصدار

شغّل الفحص الموحد قبل النشر أو تسليم أي نسخة:

```bash
python scripts/release_check.py
```

المشروع يتضمن أيضًا GitHub Actions في `.github/workflows/quality.yml` لتشغيل اختبارات Python، فحص JavaScript، Ruff، Bandit، و`pip-audit` تلقائيًا على كل Push وPull Request.
