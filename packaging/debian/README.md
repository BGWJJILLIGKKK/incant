# Install Incant on Debian

Build on the same Debian release and architecture as the target. The package includes the locked Python application and dependencies in a private virtual environment. The target needs the matching Debian Python, and APT installs FFmpeg and the other runtime dependencies. The build requires Python 3.12 or newer, uv, and the Debian packaging tools. Debian 13 ships Python 3.13.

```sh
./packaging/debian/build
sudo apt install ./dist/incant_<version>-1_<arch>.deb
sudo systemctl enable --now incant.service
systemctl status incant.service
journalctl -u incant.service -f
```

The build packages the committed tree (`git archive HEAD`), so uncommitted files never reach a package; set `INCANT_DEB_FROM_WORKTREE=1` to package the working tree for local testing. Use the filename printed by the build for your version and architecture. Installation creates the unprivileged `incant` service account. On a fresh install, start and enable the service when ready. Package upgrades restart an active service through Debian's service helper. Removing the package stops the service and keeps the publishing data and cache. APT purge removes the packaged configuration files.

Open the dashboard at `http://127.0.0.1:8765/`. The service binds to loopback by default. For a remote machine, forward the port over SSH, for example `ssh -L 8765:127.0.0.1:8765 user@host`, or put an authenticating proxy in front (below). The dashboard has no login of its own: anyone who reaches it can queue jobs that use your API keys.

Don't set `INCANT_REVIEW_HOST` in `service.env`: it is meant for the container image, where Compose publishes the port on the host's loopback only, and it bypasses the check that refuses a LAN address without an authenticating proxy.

## Installed layout

| Path | Purpose |
|---|---|
| `/usr/bin/incant` | CLI launcher for both publishing modes |
| `/usr/lib/incant/venv` | Packaged Python application and dependencies |
| `/usr/share/incant` | Bundled voices and reference lexicon |
| `/etc/incant/incant.toml` | System settings, managed by the administrator |
| `/etc/incant/lexicon.toml` | System pronunciation overrides |
| `/etc/incant/service.env` | Root-only systemd environment file for secrets and launch options |
| `/usr/lib/systemd/system/incant.service` | System service definition |
| `/var/lib/incant` | Sources, uploads, work, exports, and persistent job history |
| `/var/lib/incant/incant.local.toml` | Writable dashboard settings, applied over system settings |
| `/var/cache/incant` | Downloaded models and browser binaries |

To run a CLI job in the service's data directory, use `sudo -u incant -H incant --mode ebook /path/to/manuscript.txt`. The account needs permission to read the source. Use `INCANT_DATA` for a separate CLI library or `INCANT_CONFIG` to select another base configuration. Restart the service after saving system configuration or environment changes.

Put keys in `/etc/incant/service.env` for the service or in a mode-0600 `/var/lib/incant/.env` owned by the service account if both the daemon and CLI need them. TOML files hold only the variable names (`api_key_env`, `password_env`, `bearer_env`, `headers_env`): Incant refuses a configuration with a key in it, because the dashboard shows and edits the TOML.

To admit external sources through the daemon, set `INCANT_PATH_ROOTS=/srv/books:/mnt/library` in `service.env`, and grant the service account read access. For external **work or output** directories, also add a `ReadWritePaths=` entry with `sudo systemctl edit incant.service` and give the account write permission. The stock state and cache directories already have write access.

## Optional features

The default build includes Kokoro and native Kindle import. Kokoro fetches its models on first use. APT recommends Pandoc for DOCX, ODT, RTF, FB2 and local HTML. Install Calibre if you need its additional legacy converters. HTTP speech and LLM backends use the endpoints you configure.

Add supported extras at build time, for example:

```sh
INCANT_DEB_REVISION=2 INCANT_DEB_EXTRAS="browser qa anthropic" ./packaging/debian/build
```

A browser-enabled build needs Chromium's system libraries and browser files once per host. With that package installed, run:

```sh
sudo /usr/lib/incant/venv/bin/python -m playwright install-deps chromium
sudo -u incant -H env PLAYWRIGHT_BROWSERS_PATH=/var/cache/incant/playwright \
  /usr/lib/incant/venv/bin/python -m playwright install --only-shell chromium
```

The `torch` extra adds scanned-PDF OCR through Docling. OCR and speech QA may download their models on first use. The `nlp` extra adds Misaki pronunciation support. Use a distinct `INCANT_DEB_REVISION` when distributing a different set of extras.


## Authenticated HTTPS through Caddy

When Caddy and Incant run on the same machine, keep Incant on loopback. Edit
the existing `[review]` table in `/etc/incant/incant.toml`:

```toml
[review]
host = "127.0.0.1"
port = 8765
public_domains = ["incant.example.com"]
trusted_proxy_ips = ["127.0.0.1"]
```

Replace the example hostname with your public DNS name. Run
`sudo systemctl restart incant.service`. Incant validates the actual TCP
peer and the public Host. Requests that change state also need the matching
HTTPS Origin. Incant ignores forwarded IP headers for access control.

In Caddy, require authentication for the entire hostname. Generate a password
hash interactively with `caddy hash-password` and replace the placeholder below.
Caddy forwards the public Host by default.

```caddyfile
incant.example.com {
    basic_auth {
        operator <password-hash>
    }
    reverse_proxy 127.0.0.1:8765
}
```

A copy of this example is installed at
`/usr/share/doc/incant/Caddyfile.example`. Each authenticated user has full
dashboard access, including running jobs with the configured API keys. Keep
Incant's port outside your public firewall rules.

### Caddy on another machine

Set `host` to Incant's LAN IP and `trusted_proxy_ips` to Caddy's actual source
IP. For example, use `host = "192.168.1.10"` and
`trusted_proxy_ips = ["192.168.1.20"]`, then point Caddy at
`192.168.1.10:8765`. Keep `public_domains` set to the same public hostname.
Restrict port 8765 to Caddy at the firewall and use a private backend link.

If upgrading from a configuration that used `host = "0.0.0.0"`, either return
to `127.0.0.1` or configure both proxy settings before restarting. The daemon
rejects a LAN bind without them. After fixing a startup configuration error,
restart `incant.service` explicitly.
