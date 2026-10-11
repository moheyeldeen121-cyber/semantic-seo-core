# نشر الأداة على Render

## قبل البداية
- مفتاح Anthropic **جديد** (احذف أي مفتاح قديم ظهر في ملفات سابقة).
- ريبو GitHub **Private** فيه المشروع. ملف `.env` مش هيترفع لأنه في `.gitignore`.
- حد صرف شهري في Anthropic Console (Settings → Limits).

## الخطوات
1. ارفع المشروع على GitHub:
   ```
   git init
   git add .
   git status        # اتأكد إن .env مش ظاهر في القائمة
   git commit -m "Semantic SEO Tool v3.2"
   git branch -M main
   git remote add origin https://github.com/<user>/<repo>.git
   git push -u origin main
   ```
2. في Render: **New → Blueprint** واختار الريبو. Render هيقرأ `render.yaml` تلقائياً.
3. هيطلب منك قيمة `ANTHROPIC_API_KEY` — الصق المفتاح.
4. اضغط **Apply**. أول Build بياخد 5–10 دقايق (بيثبّت Chromium).
5. بعد ما الخدمة تبقى Live: افتح الخدمة → **Environment** → انسخ قيمة `APP_PASSWORD`
   (اتولّدت عشوائياً). غيّرها لو حابب لكلمة سر قوية تفتكرها.
6. افتح `https://<اسم-الخدمة>.onrender.com` → المتصفح هيطلب اسم المستخدم (`admin`) وكلمة السر.

## الخطة المجانية (المضبوطة حالياً في render.yaml)
- **النوم:** الخدمة بتنام بعد 15 دقيقة من غير زيارات، وأول فتح بعدها بياخد حوالي دقيقة.
  لو التحليل شغال والصفحة مفتوحة الاتصال مستمر ومش هتنام في النص.
- **Chromium مقفول** (`ENABLE_BROWSER_FALLBACK=false`) لأن الرام 512 ميجا بس.
  المواقع اللي بتعرض محتواها بالجافاسكريبت بتتقري من البيانات المضمّنة في الصفحة
  (سلة/زد/Next.js غالباً بتحطها) أو من خدمة r.jina.ai. لو فضلت ناقصة هتظهر «قراءة جزئية».
- **أبطأ:** المعالج 0.1 بس، فالتحليل الواحد ممكن ياخد كذا دقيقة. تحليل واحد في نفس الوقت.
- **Excel والكاش بيتمسحوا** لما الخدمة تنام أو تعمل Restart — نزّل التقرير فور ما يخلص.
- **750 ساعة مجانية في الشهر** للحساب كله؛ كفاية لخدمة واحدة.
- لو ظهر في Events رسالة **Out of memory**: قلّل `MAX_SOURCE_WORKERS` إلى 2.

## الترقية بعدين
غيّر `plan: free` إلى `plan: standard` و`ENABLE_BROWSER_FALLBACK` إلى `true` في `render.yaml`
واعمل Push؛ Render هيطبّق التغيير تلقائياً.

## ملاحظات عامة
- **`TRUST_PROXY_HEADERS=true` لازم يفضل مفعّل** على Render؛ من غيره كل طلبات التحليل
  هترجع 403 (Cross-origin) لأن Render بيفك HTTPS قبل ما الطلب يوصل للتطبيق.
- **تغيير أي متغير** في Environment بيعمل Restart تلقائي.
- **الدومين الخاص**: Settings → Custom Domains، وRender بيطلع شهادة HTTPS مجانية.
- **Region:** مضبوط على Frankfurt (الأقرب لمصر) ومش بيتغير بعد الإنشاء.
