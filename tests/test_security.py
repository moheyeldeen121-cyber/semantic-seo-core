import pytest

from utils.security import safe_excel_value, validate_public_http_url


def test_private_and_local_urls_are_rejected():
    for url in (
        "http://127.0.0.1/admin",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:5000/health",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            validate_public_http_url(url)


def test_url_credentials_and_unusual_ports_are_rejected():
    with pytest.raises(ValueError):
        validate_public_http_url("https://user:pass@example.com/")
    with pytest.raises(ValueError):
        validate_public_http_url("https://example.com:8080/")


def test_excel_formula_prefixes_are_neutralized():
    assert safe_excel_value("=1+1") == "'=1+1"
    assert safe_excel_value("+SUM(A1:A2)") == "'+SUM(A1:A2)"
    assert safe_excel_value("@danger") == "'@danger"
    assert safe_excel_value("normal text") == "normal text"
from services.temp_store import read_editor_content, write_editor_content


def test_editor_content_round_trip_and_rejects_invalid_tokens():
    token = write_editor_content("محتوى آمن\nSecond line")
    assert token.startswith("editor_")
    assert read_editor_content(token) == "محتوى آمن\nSecond line"
    assert read_editor_content("../editor_bad") is None
    assert read_editor_content("evidence_not_editor") is None
