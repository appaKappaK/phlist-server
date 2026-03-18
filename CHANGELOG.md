# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
