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


def fetcher(sites=None, routes=None, netrc=False):
    rec = Recorder(routes or (lambda r: httpx.Response(200, html=ARTICLE)))
    s = UrlSettings(sites=sites or {}, netrc=netrc)
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

    f, rec = fetcher({"example.com": {"bearer": "tok", "headers": {"X-Api-Key": "k"}}}, routes)
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

    f, _ = fetcher({"example.com": {"password": "pw", "username": "me"}})
    with pytest.raises(FetchError, match="plain http"):
        f.get("http://example.com/a")

    f, _ = fetcher(routes=lambda r: httpx.Response(401))
    with pytest.raises(FetchError, match=r'\[ingest\.url\.sites\."paywall\.test"\]'):
        f.get("https://paywall.test/a")
    f, _ = fetcher({"paywall.test": {"bearer": "bad"}}, routes=lambda r: httpx.Response(403))
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
        '[ingest.url.sites."example.com"]\nusername = "me"\npassword = "hunter2"\nbearer_env = "TOK"\n')
    cfg = load_config(tmp_path / "incant.toml")
    ingest = next(s for s in discover() if s.name == "ingest")
    m = instantiate(cfg, ingest, "url")
    assert m.settings.timeout_s == 10 and m.settings.sites["example.com"].password == "hunter2"
    fp = m.fingerprint()
    (tmp_path / "incant.local.toml").write_text(
        '[ingest.url.sites."example.com"]\nusername = "me"\npassword = "changed"\nbearer_env = "TOK"\n')
    assert instantiate(load_config(tmp_path / "incant.toml"), ingest, "url").fingerprint() == fp
    monkeypatch.delenv("TOK", raising=False)
    assert [ok for ok, _ in m.check()] == [False]
    assert "hunter2" not in json.dumps([msg for _, msg in m.check()])


def test_gate_and_empty_pages_refused():
    gate = ('<html><body><h1>Adult content</h1><p>This submission contains adult content. By continuing you '
            'confirm you are 18 or older.</p><button>I am 18 or older - Continue</button></body></html>')
    f, _ = fetcher(routes=lambda r: httpx.Response(200, html=gate))
    with pytest.raises(FetchError, match=r'only \d+ words .*gate page .*cookies = .*sites\."stories\.test"'):
        ingest_url("https://stories.test/s/1", f)

    shell = '<html><body><div id="root"></div><script>render()</script></body></html>'
    f, _ = fetcher(routes=lambda r: httpx.Response(200, html=shell))
    with pytest.raises(FetchError, match="JavaScript"):
        ingest_url("https://stories.test/s/2", f)

    f, _ = fetcher({"stories.test": {"bearer": "t"}}, routes=lambda r: httpx.Response(200, html=gate))
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
    assert "were sent" in str(e.value) and "JavaScript" in str(e.value) and "gate" not in str(e.value)


def test_bad_cookie_file_error_does_not_echo_contents(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("sessionid=SECRETVALUE; other=1\n")
    with pytest.raises(FetchError, match="not a Netscape-format") as e:
        fetcher({"example.com": {"cookies": str(jar)}})
    assert "SECRETVALUE" not in str(e.value)
