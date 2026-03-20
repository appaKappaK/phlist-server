# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [1.3.2] - 2026-03-20

### Fixed
- **Dashboard card URLs always match the browser's connection** — a client-side fixup now replaces any server-rendered host in card URLs with `location.host` at page load; this ensures Tailscale users see the Tailscale IP and LAN users see the LAN IP, regardless of what the server resolves

## [1.3.1] - 2026-03-20

### Fixed
- **Dashboard URL shows real LAN IP** — when `PHLIST_HOST=0.0.0.0`, the dashboard now resolves the actual outbound interface IP at startup (`DISPLAY_HOST`) and uses it in card URLs instead of showing `0.0.0.0`
- **`_VERSION` bumped to match changelog** — source version string was still `1.2.0`; updated to `1.3.0` (this release)

### Docs
- Replaced bare IP examples (`192.168.x.y`, `your-server-ip`) in README with `.PUT.IP.HERE` placeholder for consistency

## [1.3.0] - 2026-03-19

### Added
- **HTTP security headers** — `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Content-Security-Policy` added to every response via an `after_request` hook; protects the dashboard against clickjacking and MIME-sniffing
- **HTTPS documentation** — new "LAN with HTTPS (optional)" section in README covers Caddy reverse proxy setup with `tls internal`, cert trust instructions for Fedora and Debian/Ubuntu, and clarifies that Pi-hole gravity stays on plain HTTP
- 3 new tests: security headers on dashboard, security headers on API endpoint, gravity log key-leak assertion (40 → 43)

### Changed
- Gravity trigger log now records `PIHOLE_URL` instead of the full auth URL — `PIHOLE_KEY` no longer appears in the systemd journal
- Removed `--limit-request-line 0` from Gunicorn `ExecStart` — restores the default 4094-byte URL length protection (all valid phlist slugs are short)
- `dashboard.js` `renderCard()` rewritten to use `createElement`/`textContent` for all user-supplied values (`item.slug`, constructed URL); only the static SVG icon still uses `innerHTML`
- Added `UMask=0022` to systemd service `[Service]` section — makes the default file creation mode explicit
- Added `chmod 600` reminder comment to `.env.example`
- Version bumped to 1.3.0

## [1.2.0] - 2026-03-19

### Added
- System stats sidebar on the dashboard — CPU % (animated arc gauge), CPU temperature with colour coding (green/yellow/red), RAM and disk progress bars, uptime, load average, hostname. Reads from `/proc` and `/sys` with no new dependencies
- `GET /api/stats` — JSON endpoint returning cpu_pct, cpu_temp_c, mem_used_mb, mem_total_mb, mem_pct, disk_used_gb, disk_total_gb, disk_pct, uptime_s, load_avg, hostname
- Fast preview mode — `GET /lists/{slug}.txt?preview=1` returns only the first 100 lines without loading the full file (dramatically faster for multi-MB lists)
- `static/favicon.svg` — proper favicon, eliminating the browser 404 log noise
- Dashboard auto-refreshes stats every 10s; list cards sync every 5s (both already existed, sidebar now uses stats endpoint)

### Changed
- Body layout split into two columns: fixed 240px sidebar + fluid main content area
- Dashboard delete: if API key is already stored in `sessionStorage`, skips the modal and uses a native browser confirm — no re-entering the key on every delete. Modal only appears on first delete or after a 403 wrong-key error. Added "forget saved key" link in modal
- Dashboard preview now fetches `?preview=1` instead of the full file — near-instant for large lists
- `_MAX_BODY` increased from 50 MB to 2 GB (required for multi-million-domain combined lists)
- gunicorn `--timeout` increased from 30s to 300s; added `--limit-request-line 0` (handles very large request lines)
- `PHLIST_HOST` default in `.env.example` changed to `0.0.0.0` — required when Pi-hole is on the LAN but not on Tailscale. Tailscale-only setups can still use the Tailscale IP
- Copy URL button now uses `execCommand` fallback for HTTP contexts where `navigator.clipboard` is unavailable
- Version bumped to 1.2.0

## [1.1.0] - 2026-03-19

### Added
- Web dashboard at `GET /` — dark-themed status page showing all stored lists with line counts, sizes, relative timestamps, URL copy button, inline preview (first 50 lines), and auth-gated delete
- `GET /lists/` — JSON inventory endpoint returning slug, size, lines, and mtime for every stored list
- `DELETE /lists/{slug}.txt` — remove a stored list (Bearer auth required, rate limited to 10 req/min)
- Auto-gravity trigger — optionally fires a Pi-hole gravity update after each successful PUT (set `PHLIST_PIHOLE_URL`)
- python-dotenv support — loads `/etc/phlist-server/.env` then local `.env` on startup (optional, falls back gracefully if not installed)
- `PHLIST_PIHOLE_URL` and `PHLIST_PIHOLE_KEY` config vars for gravity integration

### Changed
- Dashboard served as a proper HTML template with external `static/style.css` and `static/dashboard.js` (no inline styles or scripts)
- Version bumped to 1.1.0 in server responses and startup log

## [1.0.0] - 2026-03-17

### Added
- Initial release
- `GET /health` — connection test with Bearer auth
- `PUT /lists/{slug}.txt` — receive and store a blocklist with Bearer auth
- `GET /lists/{slug}.txt` — serve a stored list (no auth, for Pi-hole gravity)
- Strict content validation — rejects non-ASCII characters (Unicode homoglyphs, zero-width chars, bidi overrides), unrecognised line formats, and lines over 1000 chars; reports up to 10 annotated violations with line numbers
- Rate limiting — 10 req/min on health check, 5 req/min on PUT (flask-limiter, in-memory)
- Atomic file writes — temp file + `os.replace()` to prevent Pi-hole reading partial files
- Slug validation — `[a-z0-9-]` only, prevents path traversal
- App factory pattern (`create_app()`) for clean test isolation
- 27 tests covering auth, CRUD, slug validation, and all content validation paths
- systemd unit with hardened service settings (ProtectSystem, dedicated user, ReadWritePaths)
- Tailscale-only binding by default (set `PHLIST_HOST` to your Orange Pi's Tailscale IP)
