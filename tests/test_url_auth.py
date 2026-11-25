"""URL ingest credentials: which requests get which secrets (httpx MockTransport, no network)."""

import base64
import json

import httpx
import pytest

from incant.config import load_config
from incant.pipeline import discover, instantiate
from incant.stages.ingest.url.url import Fetcher, FetchError, UrlSettings, ingest_url

ARTICLE = "<html><head><title>The Story</title></head><body><article>" + "".join(
    f"<p>Paragraph {i} of a story that goes on for a while, long enough to count as the main text.</p>"
    for i in range(30)) + "</article></body></html>"
LOGIN = ('<html><body><form><p>Please sign in to continue reading.</p>'
         '<input name="u"><input type="password" name="p"></form></body></html>')


class Recorder:
    """Mock site: records every request's auth-bearing headers; routes by path."""

    def __init__(self, routes):
        self.routes = routes
        self.seen = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append((req.url.host, req.headers.get("authorization"), req.headers.get("cookie"),
                          req.headers.get("x-api-key")))
        return self.routes(req)


def fetcher(sites=None, routes=None, netrc=False, allow_bogons=False):
    rec = Recorder(routes or (lambda r: httpx.Response(200, html=ARTICLE)))
    s = UrlSettings(sites=sites or {}, netrc=netrc, render="never", allow_bogons=allow_bogons)
    return Fetcher(s, transport=httpx.MockTransport(rec)), rec


def basic(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def test_basic_auth_for_site_and_subdomains_only(monkeypatch):
    monkeypatch.setenv("EX_PW", "s3cret")
    f, rec = fetcher({"example.com": {"username": "me", "password_env": "EX_PW"}})
    for url in ("https://example.com/a", "https://www.example.com/b", "https://other.org/c", "https://notexample.com/d"):
        f.get(url)
    assert [a for _, a, _, _ in rec.seen] == [basic("me", "s3cret"), basic("me", "s3cret"), None, None]


def test_credentials_not_forwarded_across_redirects(monkeypatch):
    def routes(r):
        if r.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://cdn.evil.test/page"})
        return httpx.Response(200, html=ARTICLE)

    monkeypatch.setenv("EX_TOK", "tok")
    monkeypatch.setenv("EX_KEY", "k")
    f, rec = fetcher({"example.com": {"bearer_env": "EX_TOK", "headers_env": {"X-Api-Key": "EX_KEY"}}}, routes)
    final, _, used = f.get("https://example.com/story")
    assert final == "https://cdn.evil.test/page" and used
    assert rec.seen == [("example.com", "Bearer tok", None, "k"), ("cdn.evil.test", None, None, None)]


def test_cookies_file_is_domain_scoped(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n"
                   ".example.com\tTRUE\t/\tTRUE\t0\tsession\tabc123\n")
    f, rec = fetcher({"example.com": {"cookies": str(jar)}})
    f.get("https://www.example.com/a")
    f.get("https://other.org/b")
    assert rec.seen[0][2] == "session=abc123" and rec.seen[1][2] is None


def test_errors_name_the_problem_not_the_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("NOPE", raising=False)
    f, _ = fetcher({"example.com": {"password_env": "NOPE", "username": "me"}})
    with pytest.raises(FetchError, match="environment variable NOPE is not set"):
        f.get("https://example.com/a")

    monkeypatch.setenv("EX_PW", "pw")
    f, _ = fetcher({"example.com": {"password_env": "EX_PW", "username": "me"}})
    with pytest.raises(FetchError, match="plain http"):
        f.get("http://example.com/a")

    f, _ = fetcher(routes=lambda r: httpx.Response(401))
    with pytest.raises(FetchError, match=r'\[ingest\.url\.sites\."paywall\.test"\]'):
        f.get("https://paywall.test/a")
    monkeypatch.setenv("BAD_TOK", "bad")
    f, _ = fetcher({"paywall.test": {"bearer_env": "BAD_TOK"}}, routes=lambda r: httpx.Response(403))
    with pytest.raises(FetchError, match="rejected") as e:
        f.get("https://paywall.test/a")
    assert "bad" not in str(e.value)

    with pytest.raises(FetchError, match="does not exist"):
        fetcher({"example.com": {"cookies": str(tmp_path / "missing.txt")}})


def test_login_wall_detected():
    f, _ = fetcher(routes=lambda r: httpx.Response(200, html=LOGIN))
    with pytest.raises(FetchError, match="login page"):
        ingest_url("https://news.test/story", f)
    f, _ = fetcher()
    assert ingest_url("https://news.test/story", f).title == "The Story"


def test_netrc_fallback(monkeypatch, tmp_path):
    rc = tmp_path / "netrc"
    rc.write_text("machine example.com login nm password npw\n")
    rc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(rc))
    f, rec = fetcher(netrc=True)
    f.get("https://example.com/a")
    f.get("https://other.org/a")
    assert [a for _, a, _, _ in rec.seen] == [basic("nm", "npw"), None]
    f, rec = fetcher(netrc=False)
    f.get("https://example.com/a")
    assert rec.seen[0][1] is None


def test_settings_from_local_config_and_secret_free_fingerprint(tmp_path, monkeypatch):
    (tmp_path / "incant.toml").write_text('[ingest.url]\ntimeout_s = 10\n')
    (tmp_path / "incant.local.toml").write_text(
        '[ingest.url.sites."example.com"]\nusername = "me"\npassword_env = "PW"\nbearer_env = "TOK"\n')
    cfg = load_config(tmp_path / "incant.toml")
    ingest = next(s for s in discover() if s.name == "ingest")
    m = instantiate(cfg, ingest, "url")
    assert m.settings.timeout_s == 10 and m.settings.sites["example.com"].password_env == "PW"
    fp = m.fingerprint()
    (tmp_path / "incant.local.toml").write_text(
        '[ingest.url.sites."example.com"]\nusername = "me"\npassword_env = "OTHER"\nbearer_env = "TOK"\n')
    assert instantiate(load_config(tmp_path / "incant.toml"), ingest, "url").fingerprint() == fp
    monkeypatch.setenv("PW", "hunter2")
    monkeypatch.delenv("TOK", raising=False)
    assert [ok for ok, _ in m.check()] == [True, False]
    assert "hunter2" not in json.dumps([msg for _, msg in m.check()])


def test_inline_secrets_refused(tmp_path):
    """Keys live in the environment or a .env file, never in TOML (which the dashboard shows and edits)."""
    from pydantic import ValidationError

    from incant.config import LLMConfig

    for inline in ({"password": "pw"}, {"bearer": "t"}, {"headers": {"X-Api-Key": "k"}}):
        with pytest.raises(ValidationError, match="no longer read from configuration files"):
            UrlSettings(sites={"example.com": inline})
    with pytest.raises(ValidationError, match="api_key_env"):
        LLMConfig(api_key="sk-x")
    from incant.stages.synthesize.speech_http import SpeechHTTPSettings

    with pytest.raises(ValidationError, match="api_key_env"):
        SpeechHTTPSettings(api_key="sk-x")


def test_inline_key_in_inactive_llm_profile_refused(tmp_path):
    """A profile that isn't selected is still in the TOML the dashboard shows, so its key is refused too."""
    from incant.config import load_config

    cfg = tmp_path / "incant.toml"
    cfg.write_text('[llm]\nmodel = "m"\n\n[llm.other]\nmodel = "x"\napi_key = "sk-y"\n')
    with pytest.raises(ValueError, match=r"\[other\].*api_key_env"):
        load_config(cfg)


def test_gate_and_empty_pages_refused(monkeypatch):
    gate = ('<html><body><h1>Adult content</h1><p>This submission contains adult content. By continuing you '
            'confirm you are 18 or older.</p><button>I am 18 or older - Continue</button></body></html>')
    f, _ = fetcher(routes=lambda r: httpx.Response(200, html=gate))
    with pytest.raises(FetchError, match=r'only \d+ words .*gate page .*cookies = .*sites\."stories\.test"'):
        ingest_url("https://stories.test/s/1", f)

    shell = '<html><body><div id="root"></div><script>render()</script></body></html>'
    f, _ = fetcher(routes=lambda r: httpx.Response(200, html=shell))
    with pytest.raises(FetchError, match="JavaScript"):
        ingest_url("https://stories.test/s/2", f)

    monkeypatch.setenv("INCANT_TEST_GATE_TOKEN", "t")
    f, _ = fetcher({"stories.test": {"bearer_env": "INCANT_TEST_GATE_TOKEN"}}, routes=lambda r: httpx.Response(200, html=gate))
    with pytest.raises(FetchError, match="may have expired"):
        ingest_url("https://stories.test/s/1", f)


def test_cookie_session_counts_as_credentials(tmp_path):
    """A logged-in page with no article text (loaded by JavaScript) must not be blamed on missing cookies,
    and a page that merely mentions 'adult content' somewhere isn't a gate."""
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n.stories.test\tTRUE\t/\tTRUE\t1999999999\tsess\tv\n")
    page = ('<html><body><nav>Filters: hide adult content</nav><div id="root"></div>'
            '<script>loadStory()</script></body></html>')
    f, _ = fetcher({"stories.test": {"cookies": str(jar)}}, routes=lambda r: httpx.Response(200, html=page))
    with pytest.raises(FetchError) as e:
        ingest_url("https://stories.test/s/3", f)
    assert "were sent" in str(e.value) and "gate" not in str(e.value) and "cookies = " not in str(e.value)


def test_bad_cookie_file_error_does_not_echo_contents(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("sessionid=SECRETVALUE; other=1\n")
    with pytest.raises(FetchError, match="not a Netscape-format") as e:
        fetcher({"example.com": {"cookies": str(jar)}})
    assert "SECRETVALUE" not in str(e.value)


def test_redirect_through_plain_http_is_upgraded(tmp_path):
    """www.example.com/view/N -> http://example.com/… -> https://…: the http hop is skipped, never sent secrets."""
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tsession\tabc123\n")

    def routes(r):
        if r.url.host == "www.example.com":
            return httpx.Response(301, headers={"location": "http://example.com/?url=/view/1"})
        if r.url.scheme == "http":
            return httpx.Response(301, headers={"location": f"https://example.com{r.url.raw_path.decode()}"})
        if r.url.path == "/":
            return httpx.Response(302, headers={"location": "/s/abc"})
        return httpx.Response(200, html=ARTICLE)

    f, rec = fetcher({"example.com": {"cookies": str(jar)}}, routes=routes)
    final, _, used = f.get("https://www.example.com/view/1")
    assert final == "https://example.com/s/abc" and used
    assert [h for h, *_ in rec.seen] == ["www.example.com", "example.com", "example.com"]  # no http request

    f, rec = fetcher(routes=routes)  # no secrets for the site: its redirects are followed as given
    assert f.get("https://www.example.com/view/1")[0] == "https://example.com/s/abc" and len(rec.seen) == 4


def test_browser_cookies_are_secure(tmp_path):
    from incant.stages.ingest.url.render import _cookies

    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tsession\tabc\n"
                   "localhost\tFALSE\t/\tFALSE\t0\tdev\t1\n")
    f, _ = fetcher({"example.com": {"cookies": str(jar)}, "localhost": {"cookies": str(jar)}})
    assert {c["domain"]: c["secure"] for c in _cookies(f)} == {".example.com": True, "localhost": False}


def test_private_addresses_refused_unless_allowed():
    """allow_bogons off (the default): a URL, or a redirect, to loopback, a LAN or cloud metadata is refused
    before any request is sent; on: fetched as usual."""
    for url in ("http://127.0.0.1:8765/api/config", "http://169.254.169.254/latest/meta-data/",
                "http://192.168.1.1/admin", "http://[::1]/", "http://localhost/", "http://100.64.0.1/"):
        f, rec = fetcher(allow_bogons=False)
        with pytest.raises(FetchError, match="private or reserved address"):
            f.get(url)
        assert rec.seen == []

    def routes(r):
        if r.url.host == "public.test":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/secret"})
        return httpx.Response(200, html=ARTICLE)

    f, rec = fetcher(routes=routes, allow_bogons=False)
    with pytest.raises(FetchError, match=r"10\.0\.0\.5"):
        f.get("https://public.test/story")
    assert [h for h, *_ in rec.seen] == ["public.test"]

    f, rec = fetcher(routes=routes, allow_bogons=True)
    assert f.get("https://public.test/story")[0] == "http://10.0.0.5/secret"
    assert UrlSettings().allow_bogons is False
