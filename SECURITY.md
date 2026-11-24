# Security

## Reporting a vulnerability

Report vulnerabilities privately through GitHub: **Security → Report a vulnerability** on
[github.com/BGWJJILLIGKKK/incant](https://github.com/BGWJJILLIGKKK/incant/security/advisories/new). Please don't open
a public issue for them. Expect an answer within a week; fixes go into the next release, and the advisory is
published once a fix is out.

Only the latest release is supported.

## What's in scope

incant runs locally on your own files, but some parts handle untrusted input or secrets:

- **Untrusted documents and pages**: EPUB, Kindle, PDF, Word and HTML parsing, and web pages fetched by
  `ingest=url` (including the headless browser). With `[ingest.url] allow_bogons = false` (the default), a
  submitted URL, a redirect or a rendered page reaching a private or reserved address is in scope.
- **Credentials**: logins, tokens and cookies under `[ingest.url.sites]` must only ever be sent to their own
  host (and its subdomains); API keys must never be logged, cached or written to the work directory. Keys live
  only in the environment or `.env` files; configuration files name them (`api_key_env`, `*_env`) and a key
  written into a TOML file is refused. `.env` and `.toml` files are never accepted as a book source or an
  output file.
- **The dashboard** (`--serve`) **and review page** (`--review`): neither has a login of its own. Both listen
  on 127.0.0.1 by default and refuse another address unless `[review] public_domains` and
  `trusted_proxy_ips` put an authenticating reverse proxy (such as Caddy with `basic_auth`) in front; every
  user the proxy admits has full access, including running jobs with your API keys. Requests need a matching
  `Host`, and changes a matching `Origin` and a JSON body, so other web pages and rebound DNS names can't
  drive them; responses can't be framed. (`compose.desktop.yaml` binds inside the container only and
  publishes the port on 127.0.0.1; `INCANT_REVIEW_HOST`, which it sets, bypasses the address check and is not
  for other uses.) In scope: anything that lets a web page you visit, a network peer the proxy didn't admit,
  or a book's content reach them; escapes from the allowed directories; ways to read keys through them.
- **The Docker images**: anything that lets content in a book or page affect the host beyond the mounted data
  directory.

Out of scope: what the LLM or speech servers you configure do with what you send them, and vulnerabilities in
third-party dependencies that incant doesn't make worse (report those upstream).
