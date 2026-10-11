"""Hosting safety: access protection, browser SSRF guard, and no secrets in git."""
import base64
from pathlib import Path

import pytest

pytest.importorskip("flask")

import app as app_module
import config
from services.extraction import _browser_request_allowed


def _basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_app_password_protects_every_route_except_health(monkeypatch):
    monkeypatch.setattr(config, "APP_USERNAME", "ahmed")
    monkeypatch.setattr(config, "APP_PASSWORD", "s3cret-pass")
    client = app_module.app.test_client()
    for path in ("/", "/api/meta"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"].startswith("Basic")
    assert client.post("/analyze", data={}).status_code == 401
    assert client.get("/", headers=_basic("ahmed", "wrong")).status_code == 401
    assert client.get("/api/meta", headers=_basic("ahmed", "s3cret-pass")).status_code == 200
    assert client.get("/health").status_code != 401


def test_without_password_the_app_stays_usable_locally(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    assert app_module.app.test_client().get("/api/meta").status_code == 200


def test_browser_cannot_reach_internal_addresses():
    verdicts = {}
    for internal in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:5000/admin",
                     "http://localhost/", "http://10.0.0.5/", "http://[::1]/", "file:///etc/passwd",
                     "ftp://example.com/x"):
        assert _browser_request_allowed(internal, verdicts) is False, internal
    assert _browser_request_allowed("data:text/plain,hi", verdicts) is True


def test_env_file_is_ignored_by_git():
    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore and "!.env.example" in ignore


def test_same_origin_post_works_behind_render_https_proxy(monkeypatch):
    """Render terminates HTTPS and forwards plain HTTP. Without trusting the
    proxy headers every POST from the https:// page was rejected as cross-origin."""
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    for name in ("ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setattr(config, name, "", raising=False)
    client = app_module.create_app().test_client()
    headers = {"Origin": "https://seo-tool.onrender.com", "Host": "seo-tool.onrender.com",
               "X-Forwarded-Proto": "https", "X-Forwarded-For": "41.0.0.1"}
    response = client.post("/analyze", data={"comp_urls": ["https://c1.example/a"]}, headers=headers)
    assert response.status_code != 403            # passes the origin check (then 400: no key)
    hostile = dict(headers, Origin="https://evil.example")
    assert client.post("/analyze", data={}, headers=hostile).status_code == 403


def test_render_blueprint_keeps_secrets_out_of_the_repo():
    root = Path(__file__).resolve().parents[1]
    blueprint = (root / "render.yaml").read_text(encoding="utf-8")
    assert "sync: false" in blueprint and "generateValue: true" in blueprint
    assert "sk-ant" not in blueprint
    assert 'TRUST_PROXY_HEADERS\n        value: "true"' in blueprint
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in dockerignore


def test_free_plan_profile_disables_chromium():
    import yaml
    root = Path(__file__).resolve().parents[1]
    service = yaml.safe_load((root / "render.yaml").read_text(encoding="utf-8"))["services"][0]
    env = {item["key"]: item.get("value") for item in service["envVars"]}
    assert service["plan"] == "free"
    assert env["ENABLE_BROWSER_FALLBACK"] == "false" and env["MAX_ACTIVE_JOBS"] == "1"


def test_key_follows_the_selected_engine(monkeypatch):
    import routes.analyze as analyze
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-a")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-free", raising=False)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    assert analyze._server_api_key("default") == "sk-ant-a"
    assert analyze._server_api_key("claude-sonnet-5") == "sk-ant-a"
    assert analyze._server_api_key("gemini-2.5-flash") == "AIza-free"
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    meta = app_module.app.test_client().get("/api/meta").get_json()
    assert meta["gemini_model"] == config.GEMINI_DEFAULT_MODEL and meta["response_cache"] is config.ENABLE_CACHE


def test_rerunning_the_same_request_is_free(monkeypatch, tmp_path):
    import services.claude as claude
    monkeypatch.setattr(config, "ENABLE_CACHE", True)
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path))
    calls = []

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"content": [{"type": "text", "text": "answer"}], "usage": {"input_tokens": 100}}

    monkeypatch.setattr("requests.post", lambda *a, **k: calls.append(1) or Resp())
    first = claude.call_claude_api("task", "sys", "claude-sonnet-5", "sk-ant-x", document="page", return_usage=True)
    second = claude.call_claude_api("task", "sys", "claude-sonnet-5", "sk-ant-x", document="page", return_usage=True)
    assert first[0] == second[0] == "answer" and len(calls) == 1 and second[1]["cost_usd"] == 0.0
