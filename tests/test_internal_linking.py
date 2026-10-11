"""Smart internal linking: sitemap indexes, Arabic titles vs English slugs, safety."""
import gzip

import pytest

pytest.importorskip("flask")

import routes.sitemap as sitemap

INDEX = b"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://law.example/page-sitemap.xml</loc></sitemap>
<sitemap><loc>https://law.example/post-sitemap.xml.gz</loc></sitemap>
<sitemap><loc>https://evil.example/other.xml</loc></sitemap></sitemapindex>"""
PAGES = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://law.example/</loc></url>
<url><loc>https://law.example/labor-lawyer-riyadh/</loc></url>
<url><loc>https://law.example/end-of-service-calculator/</loc></url></urlset>"""
POSTS = gzip.compress(b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.law.example/blog/arbitrary-dismissal/</loc></url>
<url><loc>https://law.example/blog/%D9%85%D9%86%D8%B5%D8%A9-%D9%86%D8%A7%D8%AC%D8%B2/</loc></url></urlset>""")
HEADS = {
    "https://law.example/": "<title>مكتب المحاماة</title>",
    "https://law.example/labor-lawyer-riyadh/": "<title>محامي قضايا عمالية في الرياض</title>",
    "https://law.example/end-of-service-calculator/": "<title>حاسبة مكافأة نهاية الخدمة</title><h1>احسب مكافأة نهاية الخدمة</h1>",
    "https://www.law.example/blog/arbitrary-dismissal/": "<title>التعويض عن الفصل التعسفي في نظام العمل</title>",
}


def fake_fetch(url, limit):
    if url.endswith("sitemap_index.xml"):
        return INDEX
    if url.endswith("page-sitemap.xml"):
        return PAGES
    if url.endswith("post-sitemap.xml.gz"):
        return POSTS
    if url in HEADS:
        return HEADS[url].encode()
    raise AssertionError(f"unexpected fetch {url}")


def test_index_is_followed_gzip_read_and_other_sites_ignored():
    urls = sitemap.collect_urls("https://law.example/sitemap_index.xml", fetch=fake_fetch)
    assert "https://law.example/labor-lawyer-riyadh/" in urls
    assert "https://www.law.example/blog/arbitrary-dismissal/" in urls
    assert "https://law.example/blog/%D9%85%D9%86%D8%B5%D8%A9-%D9%86%D8%A7%D8%AC%D8%B2/" in urls  # kept fetchable
    assert not any("evil.example" in url for url in urls)


def test_arabic_entities_match_pages_by_title_and_arabic_slug(monkeypatch):
    monkeypatch.setattr(sitemap, "_get", fake_fetch)
    monkeypatch.setattr(sitemap, "validate_public_http_url", lambda url, language="ar": url)
    from app import app
    import config
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    response = app.test_client().post("/api/sitemap-match", json={
        "sitemap_url": "https://law.example/sitemap_index.xml",
        "exclude_url": "https://law.example/labor-lawyer-riyadh/",
        "entities": [{"name": "مكافأة نهاية الخدمة", "lsi": ["مستحقات نهاية الخدمة"]},
                     {"name": "قضايا الفصل التعسفي", "lsi": ["الفصل التعسفي"]},
                     {"name": "منصة ناجز", "lsi": []},
                     {"name": "محامي قضايا عمالية", "lsi": []},
                     {"name": "الملكية الفكرية", "lsi": []}]})
    data = response.get_json()
    matches = data["matches"]
    assert matches["مكافأة نهاية الخدمة"][0]["url"].endswith("end-of-service-calculator/")
    assert matches["قضايا الفصل التعسفي"][0]["anchor"] == "الفصل التعسفي"
    assert matches["منصة ناجز"][0]["display_url"].endswith("منصة-ناجز/")    # matched via the decoded Arabic slug
    assert "محامي قضايا عمالية" not in matches                          # the analysed page itself is excluded
    assert "الملكية الفكرية" not in matches
    assert all(not page["url"].rstrip("/").endswith("law.example") for pages in matches.values() for page in pages)
    assert data["total_urls"] == 5


def test_internal_addresses_are_refused():
    from app import app
    import config
    config_password = config.APP_PASSWORD
    config.APP_PASSWORD = ""
    try:
        response = app.test_client().post("/api/sitemap-match", json={
            "sitemap_url": "http://169.254.169.254/latest/meta-data/", "entities": ["x"]})
    finally:
        config.APP_PASSWORD = config_password
    assert response.status_code == 400
