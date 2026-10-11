"""Reading-order engine: DOM order, no sidebar intermixing, no lost sections."""
from services.html_reader import read_html
import services.extraction as extraction

SIDEBAR_FIRST = """<html><head><title>دليل السيو | موقعي</title></head><body>
<header class="site-header"><h1 class="logo">موقعي</h1><nav><a href="/">الرئيسية</a><a href="/a">مقالات</a><a href="/b">اتصل</a></nav></header>
<div class="wrap">
 <div class="col-side"><h3>الأكثر قراءة</h3><ul><li><a href="/x">مقال 1</a></li><li><a href="/y">مقال 2</a></li><li><a href="/z">مقال 3</a></li></ul></div>
 <div class="post">
  <div class="post-head"><h1>دليل السيو الشامل</h1></div>
  <div class="post-body">
    مقدمة مكتوبة مباشرة داخل div بدون وسم p وتشرح أهمية تحسين محركات البحث للمواقع التجارية.<br><br>
    فقرة ثانية بعد سطرين فارغين تشرح نية البحث والكلمات المفتاحية بشكل مفصل.
    <h2>السيو التقني</h2>
    <p>السيو التقني يهتم بسرعة الموقع والفهرسة والبيانات المنظمة وخرائط الموقع، وهو ركيزة أساسية.</p>
    <ul><li>سرعة التحميل<ul><li>ضغط الصور</li></ul></li><li>ملف robots</li></ul>
    <h2>الأسئلة الشائعة</h2>
    <div class="faq"><button aria-expanded="false">ما الفرق بين السيو والتسويق بالمحتوى؟</button>
      <div class="answer" style="display:none">السيو يركز على الظهور في محركات البحث بينما التسويق بالمحتوى أوسع ويشمل القنوات الاجتماعية.</div></div>
  </div>
  <div class="related-posts"><h3>مقالات ذات صلة</h3><a href="/1">أ</a><a href="/2">ب</a><a href="/3">ج</a></div>
 </div>
</div><footer><p>جميع الحقوق محفوظة</p></footer></body></html>"""


def test_article_is_read_in_dom_order_without_sidebar_or_chrome():
    text = read_html(SIDEBAR_FIRST).text
    order = ["[H1]: دليل السيو الشامل", "مقدمة مكتوبة مباشرة", "فقرة ثانية بعد سطرين",
             "[H2]: السيو التقني", "السيو التقني يهتم", "• سرعة التحميل", "• ضغط الصور",
             "[H2]: الأسئلة الشائعة", "ما الفرق بين السيو", "السيو يركز على الظهور"]
    positions = [text.index(fragment) for fragment in order]
    assert positions == sorted(positions), "blocks must follow the DOM reading order"
    for noise in ("الأكثر قراءة", "مقالات ذات صلة", "جميع الحقوق", "الرئيسية", "[H1]: موقعي"):
        assert noise not in text
    assert text.count("ضغط الصور") == 1, "nested list items must not be duplicated"


def test_whole_page_form_wrapper_and_layout_table_are_not_deleted():
    para = "نص طويل عن الموضوع الرئيسي ويحتوي على معلومات مفيدة للقارئ. " * 6
    html = (f"<html><body><form id='aspnetForm'><table><tr><td><h1>عنوان الصفحة</h1><p>{para}</p>"
            f"<p>{para}</p></td></tr></table></form></body></html>")
    text = read_html(html).text
    assert "[H1]: عنوان الصفحة" in text
    assert "نص طويل عن الموضوع" in text


def test_article_split_across_sibling_containers_is_read_completely():
    intro = "وصف المقدمة للمنتج بجودة عالية ومواصفات دقيقة. " * 5
    spec = "المواصفات التفصيلية للمنتج والوزن والأبعاد. " * 6
    faq = "إجابات الأسئلة الشائعة حول المنتج والضمان. " * 6
    html = (f"<html><body><main><h1>منتج</h1><div class='intro'><p>{intro}</p></div>"
            f"<div class='x'><a href='/a'>ر</a><a href='/b'>ر</a><a href='/c'>ر</a></div>"
            f"<div class='details'><h2>المواصفات</h2><p>{spec}</p></div>"
            f"<div class='details'><h2>الأسئلة</h2><p>{faq}</p></div></main></body></html>")
    text = read_html(html).text
    for fragment in ("[H1]: منتج", "وصف المقدمة", "[H2]: المواصفات", "المواصفات التفصيلية", "[H2]: الأسئلة", "إجابات الأسئلة"):
        assert fragment in text


def test_repeated_legitimate_headings_survive_dedupe():
    body = "".join(f"<h2>منتج {i}</h2><h3>المميزات</h3><p>{'ميزة مهمة وتفصيلية للمنتج رقم %d. ' % i * 4}</p>" for i in range(3))
    text = read_html(f"<html><body><article><h1>مقارنة</h1>{body}</article></body></html>").text
    assert text.count("[H3]: المميزات") == 3


def test_utf8_arabic_without_charset_is_decoded_and_mojibake_repaired():
    class Response:
        headers = {"Content-Type": "text/html"}
        encoding = "ISO-8859-1"
    raw = "<html><body><p>تحسين محركات البحث</p></body></html>".encode("utf-8")
    assert "تحسين محركات البحث" in extraction._decode(raw, Response())
    broken = "تحسين محركات البحث للمتاجر الإلكترونية".encode("utf-8").decode("cp1252", errors="replace")
    assert extraction._repair_mojibake(broken * 3).startswith("تحسين")


def test_waf_challenges_are_detected_but_normal_pages_are_not():
    challenge = "<html><head><title>Just a moment...</title></head><body>cf_chl_opt</body></html>"
    assert extraction._detect_block(403, {"Server": "cloudflare"}, challenge, 0) == "Cloudflare"
    assert extraction._detect_block(200, {"cf-mitigated": "challenge"}, "", 0) == "Cloudflare"
    assert extraction._detect_block(403, {}, "Forbidden", 0) == "HTTP 403"
    # A real article that embeds a reCAPTCHA contact form is NOT blocked.
    article = "<html><body>" + "<p>نص مقال حقيقي طويل.</p>" * 200 + "<div class='g-recaptcha'></div></body></html>"
    assert extraction._detect_block(200, {}, article, 5000) == ""
    assert extraction._detect_block(404, {}, "Not found", 0) == ""


def test_blocked_url_raises_blocked_error_and_is_never_cached(monkeypatch):
    calls = {"fetch": 0}

    def fake_fetch(session, url, headers=None, language="ar"):
        calls["fetch"] += 1
        raise extraction._HTTPStatusError(403, {"Server": "cloudflare"}, b"<title>Just a moment...</title>")

    monkeypatch.setattr(extraction, "validate_public_http_url", lambda url, language="ar": url)
    monkeypatch.setattr(extraction, "_fetch_public", fake_fetch)
    monkeypatch.setattr(extraction, "_render_with_playwright", lambda *a, **k: "")
    monkeypatch.setattr(extraction.config, "ENABLE_JINA_FALLBACK", True)
    monkeypatch.setattr(extraction, "_fetch_with_jina", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reader must not be used for blocked pages")))
    result = extraction.extract_url("https://blocked.example/p", deep_read=True)
    assert result.status == "blocked" and result.provider == "Cloudflare" and result.text == ""
    try:
        extraction.extract_text_from_url("https://blocked.example/p", True)
    except extraction.BlockedPageError as exc:
        assert "محجوب" in str(exc)
    else:
        raise AssertionError("BlockedPageError expected")
    assert calls["fetch"] == 2, "blocked results must not be cached"


def test_recovered_blocks_are_inserted_at_their_dom_position():
    from services.html_reader import blocks_to_text, merge_in_order
    html = ("<html><body><article><h1>عنوان</h1>"
            + "".join(f"<p>{'فقرة رقم %d بمحتوى طويل ومفيد للقارئ. ' % i * 4}</p>" for i in range(4))
            + "</article></body></html>")
    read = read_html(html)
    trimmed = [block for block in read.blocks if "فقرة رقم 2" not in block.text]
    missing = next(block.text for block in read.page_blocks if "فقرة رقم 2" in block.text)
    merged = blocks_to_text(merge_in_order(trimmed, read.page_blocks, [missing]))
    assert merged.index("فقرة رقم 1") < merged.index("فقرة رقم 2") < merged.index("فقرة رقم 3")
