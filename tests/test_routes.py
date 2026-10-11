import pytest

pytest.importorskip("flask")

from routes.analyze import _NamedBytesIO, _parse_serp_csv


def test_uploaded_memory_file_is_seekable():
    upload = _NamedBytesIO(b"abcdef", "sample.txt")
    assert upload.read(3) == b"abc"
    upload.seek(0)
    assert upload.read() == b"abcdef"
    assert upload.filename == "sample.txt"


def test_serp_csv_recognizes_named_columns_and_deduplicates():
    payload = (
        "title,url,description\n"
        "First,https://example.com/a,One\n"
        "Duplicate,https://example.com/a,Two\n"
        "Second,https://example.org/b,Three\n"
    ).encode()
    rows = _parse_serp_csv(payload, limit=10)
    assert rows == [
        {"url": "https://example.com/a", "title": "First", "desc": "One", "content": ""},
        {"url": "https://example.org/b", "title": "Second", "desc": "Three", "content": ""},
    ]


def test_runtime_meta_and_observability_headers():
    from app import app

    client = app.test_client()
    response = client.get("/api/meta", headers={"X-Request-ID": "test-request-123"})
    assert response.status_code == 200
    assert response.get_json()["features"]["deterministic_evidence_scoring"] is True
    assert response.headers["X-Request-ID"] == "test-request-123"
    assert response.headers["Server-Timing"].startswith("app;dur=")
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_cross_origin_post_is_blocked_including_different_port():
    from app import app

    client = app.test_client()
    response = client.post(
        "/cancel/abcdefgh",
        headers={"Origin": "http://localhost:3000", "Host": "localhost"},
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "origin_blocked"
