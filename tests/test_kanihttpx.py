import sys
import types


if "httpx" not in sys.modules:
    fake_httpx = types.ModuleType("httpx")

    class Timeout:
        def __init__(self, value):
            self.value = value

    class HTTPTransport:
        def __init__(self, retries):
            self.retries = retries

    fake_httpx.Timeout = Timeout
    fake_httpx.HTTPTransport = HTTPTransport
    sys.modules["httpx"] = fake_httpx

if "httpx_html" not in sys.modules:
    fake_httpx_html = types.ModuleType("httpx_html")

    class HTMLSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.headers = {}
            self.cookies = {}

    fake_httpx_html.HTMLSession = HTMLSession
    sys.modules["httpx_html"] = fake_httpx_html

import pytest

import kanihttpx


class DummyResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class DummySession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.headers = {}
        self.cookies = {"sid": "abc"}
        self.proxies = None
        self.verify = True

        self.get_response = DummyResponse(200)
        self.post_response = DummyResponse(200)
        self.get_exception = None
        self.post_exception = None

    def get(self, url, *args, **kwargs):
        self.last_get = (url, args, kwargs)
        if self.get_exception:
            raise self.get_exception
        return self.get_response

    def post(self, url, *args, **kwargs):
        self.last_post = (url, args, kwargs)
        if self.post_exception:
            raise self.post_exception
        return self.post_response

    def put(self, url, *args, **kwargs):
        return {"method": "put", "url": url, "kwargs": kwargs}

    def delete(self, url, *args, **kwargs):
        return {"method": "delete", "url": url, "kwargs": kwargs}

    def close(self):
        self.closed = True


class DummyMailer:
    def __init__(self):
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)


def test_init_passes_timeout_transport_headers_and_proxy(monkeypatch):
    created = {}

    def fake_session(**kwargs):
        created["session"] = DummySession(**kwargs)
        return created["session"]

    monkeypatch.setattr(kanihttpx, "HTMLSession", fake_session)

    client = kanihttpx.KaniRequests(
        headers={"User-Agent": "test-agent"},
        proxy={"https://": "http://127.0.0.1:8080"},
        default_timeout=5,
        max_retries=7,
    )

    session = created["session"]
    assert "transport" in session.kwargs
    assert session.kwargs["headers"] == {"User-Agent": "test-agent"}
    assert session.kwargs["verify"] is False
    assert session.kwargs["proxies"] == {"https://": "http://127.0.0.1:8080"}
    assert client.session.proxies == {"https://": "http://127.0.0.1:8080"}
    assert client.session.verify is False


def test_get_sets_cookies_and_returns_response(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(kanihttpx, "HTMLSession", lambda **kwargs: session)
    client = kanihttpx.KaniRequests()

    response = client.get("https://example.com", params={"a": 1})

    assert response.status_code == 200
    assert session.last_get[2]["cookies"] == {"sid": "abc"}
    assert session.last_get[2]["params"] == {"a": 1}


def test_get_sends_mail_when_status_not_200(monkeypatch):
    session = DummySession()
    session.get_response = DummyResponse(503)
    monkeypatch.setattr(kanihttpx, "HTMLSession", lambda **kwargs: session)
    client = kanihttpx.KaniRequests()

    mailer = DummyMailer()
    client.set_error_mailer(mailer, "ops@example.com", "status alert")

    response = client.get("https://example.com")

    assert response.status_code == 503
    assert len(mailer.calls) == 1
    assert mailer.calls[0]["to"] == "ops@example.com"
    assert "status_code is not 200" in mailer.calls[0]["contents"]


def test_get_sends_mail_and_reraises_on_exception(monkeypatch):
    session = DummySession()
    session.get_exception = RuntimeError("boom")
    monkeypatch.setattr(kanihttpx, "HTMLSession", lambda **kwargs: session)
    client = kanihttpx.KaniRequests()

    mailer = DummyMailer()
    client.set_error_mailer(mailer, "ops@example.com", "error alert")

    with pytest.raises(RuntimeError, match="boom"):
        client.get("https://example.com")

    assert len(mailer.calls) == 1
    assert "traceback.format_exc" in mailer.calls[0]["contents"]


def test_mount_raises_not_implemented(monkeypatch):
    monkeypatch.setattr(kanihttpx, "HTMLSession", lambda **kwargs: DummySession(**kwargs))
    client = kanihttpx.KaniRequests()

    with pytest.raises(NotImplementedError, match="no longer supported"):
        client.mount("https://", {})


def test_put_delete_and_cookie_helpers(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(kanihttpx, "HTMLSession", lambda **kwargs: session)
    client = kanihttpx.KaniRequests()

    result_put = client.put("https://example.com", json={"x": 1})
    result_delete = client.delete("https://example.com")

    assert result_put["kwargs"]["cookies"] == {"sid": "abc"}
    assert result_delete["kwargs"]["cookies"] == {"sid": "abc"}

    assert client.cookies_to_dict() == {"sid": "abc"}
    client.add_cookies({"new": "cookie"})
    assert client.cookies_to_dict() == {"sid": "abc", "new": "cookie"}


def test_open_html_in_browser_runs_xdg_open(monkeypatch):
    state = {}

    class FakeTmpFile:
        name = "/tmp/fake.html"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def write(self, value):
            state["written"] = value

        def flush(self):
            state["flushed"] = True

    monkeypatch.setattr(kanihttpx.tempfile, "NamedTemporaryFile", lambda suffix: FakeTmpFile())
    monkeypatch.setattr(kanihttpx.os, "system", lambda cmd: state.setdefault("cmd", cmd))
    monkeypatch.setattr(kanihttpx.time, "sleep", lambda seconds: state.setdefault("sleep", seconds))

    kanihttpx.open_html_in_browser(b"<html></html>")

    assert state["written"] == b"<html></html>"
    assert state["flushed"] is True
    assert "xdg-open /tmp/fake.html" in state["cmd"]
    assert state["sleep"] == 5
