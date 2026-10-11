# الخطة الشاملة لتطوير Semantic SEO Tool

## الرؤية

تحويل الأداة من محلل دلالي قوي إلى منصة قرار محتوى موثوقة: سريعة، قابلة للتفسير، آمنة، قابلة للقياس، وتُظهر حدود الثقة بدل إخفائها. معيار النجاح ليس كثرة الكيانات أو طول الهيكلة، بل جودة الدليل، ثبات النتيجة، سهولة اتخاذ القرار، وانخفاض تكلفة التشغيل والخطأ.

## ما تم تنفيذه في النسخة 2.0

### 1. الدقة والثقة

- إضافة طبقة **Deterministic Evidence Scoring** بعد تحليل AI. كل كيان يحصل على `evidence_score` و`evidence_level` اعتمادًا على التطبيع العربي، تطابق العبارة، مرادفات LSI، وتقارب الكلمات داخل النص الحقيقي.
- إضافة `confidence` و`evidence_level` لكل فجوة دلالية، مبنيين على عدد المصادر المستقلة، التكرار، والأولوية.
- عدم حذف مخرجات AI بصمت: العناصر ضعيفة الدليل تبقى مرئية بعلامة واضحة حتى يراجعها المستخدم.
- لوحة جودة شاملة: درجة من 100، نسبة الدعم النصي، التحقق المباشر، العناصر غير المتحققة، الفجوات عالية الثقة، عدد المصادر، زمن المعالجة، وتحذيرات قابلة للتنفيذ.
- الحفاظ على LSI أثناء مرحلة التحقق الاختيارية، بعد أن كان من الممكن فقدها عند إعادة بناء الفجوات.
- إضافة Quality sheet وأعمدة الدليل والثقة إلى Excel.

### 2. UX وتجربة القرار

- شريط حالة للخادم والاتصال، مع الإصدار والسعة في tooltip.
- فحص Preflight مباشر قبل التشغيل: عدد المصادر، حجم الرفع، كثافة العمل، تجاوز حد الملف أو المنافسين، وحالة الاتصال.
- تعطيل الأزرار تلقائيًا عند وجود مدخل غير صالح بدل اكتشافه بعد بدء العملية.
- إظهار/إخفاء مفتاح API ومسحه من الجلسة بسهولة.
- حفظ الإعدادات غير الحساسة فقط محليًا: الموديل، اللغة، Deep Read، والتحقق والخطة.
- تصدير JSON منقّى من tokens المؤقتة، بجانب Excel وWord.
- عرض نسبة الدليل لكل كيان ونسبة الثقة لكل فجوة داخل الجداول.
- Skip link وتحسينات responsive وARIA و`prefers-reduced-motion`.
- Watchdog للـSSE: إذا انقطع التحديث طويلًا تُلغى العملية على الخادم والمتصفح بدل تركها عالقة.

### 3. الأمان

- حماية Same-Origin للطلبات المتغيرة مع CORS صريح.
- `cancel_token` سري مستقل لكل عملية؛ لا يكفي تخمين `job_id` لإلغاء عمل مستخدم آخر.
- منع اصطدام معرفات العمليات وتحديد `MAX_ACTIVE_JOBS` لحماية الذاكرة وحصة Nara.
- HSTS عند HTTPS، وسياسة Permissions أشد، وRequest ID لكل استجابة.
- عدم إدراج tokens المؤقتة في JSON المُصدّر.
- دعم ProxyFix اختياري للنشر خلف Render/Nginx/Cloudflare.
- Rate limiter قابل لاستخدام Redis في النشر متعدد instances.

### 4. التشغيل والمراقبة

- Application factory لتسهيل الاختبارات والنشر.
- `/health` للحيوية، `/ready` للجاهزية، و`/api/meta` للحدود والميزات العامة.
- `X-Request-ID` و`Server-Timing` في كل استجابة، وإظهار Request ID للمستخدم عند الخطأ.
- Healthcheck داخل Docker وتحويل Render إلى `/ready`.
- رقم إصدار موحد `2.0.0` في التطبيق والبيئة وملف `VERSION`.

### 5. الجودة البرمجية

- فصل منطق الجودة في `services/quality.py` واختباره مستقلًا.
- 31 اختبارًا ناجحًا واختبار واحد مشروط بوجود Flask في بيئة الاختبار.
- فحص syntax للواجهة عبر Node، وتجميع Python كامل دون أخطاء.

## خارطة الطريق للوصول إلى منصة عالمية

### المرحلة A — Benchmark دقة علمي

1. إنشاء Golden Dataset عربية/إنجليزية من 200–500 صفحة في قطاعات مختلفة، مع كيانات وفجوات يراجعها خبراء.
2. قياس Precision وRecall وF1 لكل موديل، ولكل نوع كيان، ولكل طول مصدر.
3. تشغيل Model Router تلقائي يختار الموديل حسب اللغة والطول والقطاع والتكلفة، لا حسب قائمة ثابتة فقط.
4. إضافة Ensemble اختياري: نموذج استخراج + نموذج تدقيق مستقل، مع حل التعارض بقواعد حتمية.
5. تقييم المعايرة: يجب أن تتطابق درجة الثقة 80% مع صحة فعلية تقارب 80% على Benchmark.

**مؤشرات النجاح:** Entity F1، Gap precision، hallucination rate، تكلفة كل مصدر، وزمن P95.

### المرحلة B — هندسة المهام والاعتمادية

1. نقل التحليلات الطويلة إلى Queue حقيقية مثل Redis + RQ/Celery مع worker isolation.
2. حفظ حالة المهمة في Redis/PostgreSQL، ودعم الاستئناف بعد restart.
3. Idempotency keys لمنع تكرار التحليل المدفوع عند إعادة الإرسال.
4. Circuit breaker مستقل لكل مزود AI أو Reader، مع health score واختيار fallback.
5. Distributed tracing عبر OpenTelemetry، ومقاييس Prometheus: latency، 429، retries، extraction quality، token usage، queue depth.
6. تخزين exports في object storage مشفّر بروابط موقعة قصيرة العمر بدل filesystem عند التوسع الأفقي.

### المرحلة C — أمان المؤسسات

1. حسابات مستخدمين وSSO/OIDC، وعزل tenant، وRBAC للمشروعات والتقارير.
2. تشفير secrets عبر managed secret store، وعدم إرسال API key من المتصفح في وضع المؤسسة.
3. Malware scanning للملفات، وسياسة MIME + magic bytes، وsandbox منفصل لمعالجة Office/HTML.
4. Egress proxy يسمح فقط بالنطاقات والبروتوكولات المطلوبة، مع DNS pinning ضد rebinding.
5. Audit log غير قابل للتلاعب، وسياسات retention وحذف ذاتي وData Processing Agreement.
6. SAST/DAST وdependency scanning وSBOM وتوقيع صور Docker ونتائج builds.

### المرحلة D — UX احترافي كامل

1. Wizard من ثلاث خطوات: الهدف → المصادر → إعدادات الجودة، مع defaults ذكية حسب الحالة.
2. History للمشروعات، مقارنة نسختين، ومتابعة تحسن Content Score عبر الزمن.
3. Virtualized tables لآلاف الكيانات، saved views، sorting متعدد، وفلاتر متقدمة.
4. Evidence-first workflow: كل توصية تفتح الجملة والمصدر وموضعها، مع قبول/رفض وتعليق.
5. Collaboration: مشاركة تقرير read-only، تعليقات، assignments، ونسخة مراجعة.
6. Onboarding تفاعلي، sample project، وempty states تعليمية بدل واجهة إعدادات فقط.
7. WCAG 2.2 AA مع اختبارات آلية ويدوية وقارئات شاشة.

### المرحلة E — ذكاء المحتوى

1. Search Intent classifier متعدد النوايا مع confidence وevidence.
2. Topic graph حقيقي: nodes للكيانات وedges للعلاقات، مع centrality وcoverage paths.
3. Cannibalization detector عبر مقالات الموقع كله، لا مقال منفرد.
4. Internal linking recommendations مبنية على embeddings وbusiness value.
5. Schema.org recommendations وFAQ/Product/Service validation.
6. Content decay monitor يراقب تغيّر SERP والفجوات والتنبيهات بمرور الوقت.
7. ربط GSC/GA4 لإعطاء الأولوية للفجوات حسب impressions وCTR والتحويل، لا تكرار المنافسين فقط.

### المرحلة F — الأداء والتكلفة

1. Content-addressed cache على مستوى النص والجزء والـprompt، مع versioned cache keys.
2. Batch scheduling حسب token budget، وdynamic concurrency حسب 429 والlatency.
3. Streaming partial results: إظهار المصادر والكيانات المكتملة فورًا بدل انتظار التقرير كله.
4. فصل extraction workers عن AI workers وعن export workers.
5. ضغط payloads وpagination للأدلة، وIndexedDB للنتائج الكبيرة في المتصفح.
6. ميزانية تكلفة يحددها المستخدم: اقتصادي، متوازن، أقصى دقة، مع تقدير قبل التشغيل.

## بوابات إصدار إلزامية

لا تُنشر نسخة إنتاجية جديدة قبل اجتياز:

- اختبارات unit + integration + end-to-end.
- Benchmark دقة ثابت ومقارنة regression بالإصدار السابق.
- فحص أمني وdependency audit وcontainer scan.
- Load test عند P95 المستهدف وسعة الذاكرة المحددة.
- Accessibility audit.
- Rollback plan، migration plan، ونسخة احتياطية.
- Canary release ثم مراقبة الأخطاء والتكلفة والدقة.

## ترتيب التنفيذ المقترح

1. Golden Dataset وقياس الدقة.
2. Queue + persistence + observability.
3. Evidence workflow وHistory.
4. Enterprise security وعزل المستخدمين.
5. تكاملات GSC/GA4 وtopic graph.
6. Collaboration وmonitoring المستمر.

هذه الأولوية تمنع بناء خصائص كثيرة فوق أساس لا يمكن قياس دقته أو تشغيله بثبات.
