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
  `ingest=url` (including the headless browser).
- **Credentials**: logins, tokens and cookies under `[ingest.url.sites]` must only ever be sent to their own
  host (and its subdomains); API keys must never be logged, cached or written to the work directory.
- **The review page** (`--review`): it has no authentication and listens on 127.0.0.1 by default. Anyone who can
  reach it can edit a book's script and casting, so binding it to another address (`--host 0.0.0.0`) is at your
  own risk. Bugs that let a web page you visit reach it, or that escape the book's work directory, are in scope.
- **The Docker images**: anything that lets content in a book or page affect the host beyond the mounted data
  directory.

Out of scope: what the LLM or speech servers you configure do with what you send them, and vulnerabilities in
third-party dependencies that incant doesn't make worse (report those upstream).
