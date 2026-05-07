# phlist-server

A lightweight Flask server that receives Pi-hole blocklists pushed from the [phlist](https://github.com/appaKappaK/phlist) desktop app and serves them as plain-text URLs that Pi-hole can subscribe to via gravity.

## How it works

1. You build and push a combined blocklist from the phlist desktop app
2. phlist-server stores it as a `.txt` file on disk
3. Pi-hole fetches the list via a plain HTTP URL
4. Pi-hole runs gravity — done

## API

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/` | GET | None | Web dashboard |
| `/health` | GET | Bearer token | Connection test |
| `/api/stats` | GET | None | System stats JSON (CPU, RAM, disk, uptime, temp) |
| `/lists/` | GET | None | JSON inventory of all stored lists |
| `/lists/{slug}.txt` | PUT | Bearer token | Receive & store a blocklist (up to 2 GB) |
| `/lists/{slug}.txt` | GET | None | Serve list to Pi-hole (`?preview=1` for first 100 lines) |
| `/lists/{slug}.txt` | DELETE | Delete password bearer token | Delete a stored list |

## Quick start (local / dev)

```bash
git clone https://github.com/appaKappaK/phlist-server.git
cd phlist-server
pip install -r requirements.txt

cp .env.example .env
# Edit .env — set PHLIST_API_KEY, PHLIST_DELETE_PWD, and PHLIST_HOST

python phlist_server.py
```

## Deployment on Orange Pi 2W (or any Linux SBC)

### 1. Copy files

```bash
sudo mkdir -p /opt/phlist-server
sudo cp phlist_server.py /opt/phlist-server/
sudo cp -r templates static /opt/phlist-server/
python3 -m venv /opt/phlist-server/venv
/opt/phlist-server/venv/bin/pip install flask flask-limiter python-dotenv
```

### 2. Create config

```bash
sudo mkdir -p /etc/phlist-server
sudo cp .env.example /etc/phlist-server/.env
sudo nano /etc/phlist-server/.env
# Set PHLIST_API_KEY
# Set PHLIST_DELETE_PWD for dashboard deletes
# Keep PHLIST_HOST=127.0.0.1 for local-only access
# Set PHLIST_HOST=100.x.y.z to restrict to Tailscale peers only
# Set PHLIST_HOST=0.0.0.0 only if Pi-hole must reach this server over your LAN
sudo chmod 600 /etc/phlist-server/.env
```

### 3. Create system user and list directory

```bash
sudo useradd -r -s /bin/false phlist
sudo mkdir -p /var/lib/phlist/lists
sudo chown phlist:phlist /var/lib/phlist/lists
```

### 4. Install systemd service

```bash
sudo cp systemd/phlist-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now phlist-server
sudo systemctl status phlist-server
```

### Updating an existing deployment

From a fresh checkout on the server:

```bash
git pull
sudo scripts/update-deployed.sh
```

The updater replaces only the deployed app files in `/opt/phlist-server`, refreshes the virtualenv dependencies, reinstalls the systemd unit, restarts `phlist-server`, and runs an authenticated `/health` check. It does not overwrite `/etc/phlist-server/.env` or `/var/lib/phlist/lists`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PHLIST_API_KEY` | *(required)* | Bearer token for authentication. Generate with: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `PHLIST_DELETE_PWD` | falls back to `PHLIST_API_KEY` | Password required by the web dashboard delete modal and DELETE endpoint |
| `PHLIST_LIST_DIR` | `/var/lib/phlist/lists` | Directory where blocklist `.txt` files are stored |
| `PHLIST_HOST` | `127.0.0.1` | IP address to bind to. `127.0.0.1` is local-only. Use your Tailscale IP (`100.x.y.z`) to restrict access to Tailscale peers. Use `0.0.0.0` only when another LAN device, such as Pi-hole, must connect directly; it listens on every network interface. |
| `PHLIST_PORT` | `8765` | TCP port |
| `PHLIST_PIHOLE_URL` | *(unset)* | Optional: Pi-hole base URL for auto-gravity trigger after each push (e.g. `http://pi.hole`) |
| `PHLIST_PIHOLE_KEY` | *(unset)* | Optional: Pi-hole API key used with `PHLIST_PIHOLE_URL` |

## Network

Choose the narrowest bind address that still lets Pi-hole reach the server:

**Local-only:** Keep `PHLIST_HOST=127.0.0.1`. Only software running on the same machine can connect. This is the safest default, but a Pi-hole on another device cannot subscribe to it directly.

**Tailscale-only:** Set `PHLIST_HOST` to your Tailscale IP (`100.x.y.z`). Pi-hole subscribes via that same Tailscale IP:
```
http://100.x.y.z:8765/lists/slug.txt
```
No TLS needed at the Flask level — Tailscale handles encryption end-to-end.

**LAN / all-interfaces:** Set `PHLIST_HOST=0.0.0.0` only when Pi-hole is on another LAN device and cannot reach this server through Tailscale. `0.0.0.0` is not a private LAN address; it means "listen on every network interface", including Wi-Fi, Ethernet, VPN, and any other active interface. Pi-hole subscribes via your server's LAN IP:
```
http://.PUT.IP.HERE:8765/lists/slug.txt
```
If you use `0.0.0.0`, protect the host with your firewall and do not expose port `8765` to the internet.

## Security

- **Bearer token auth** with constant-time comparison (`hmac.compare_digest`) — prevents timing attacks
- **HTTP security headers** — `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Content-Security-Policy` on every response; protects the dashboard against clickjacking and MIME-sniffing
- **Strict content validation** — every uploaded line must be ASCII-only and match a known blocklist format; non-ASCII characters (Unicode homoglyphs, zero-width chars, bidi overrides) are rejected with a detailed error showing which line failed
- **Rate limiting** — 10 req/min on health check, 5 req/min on PUT uploads
- **Atomic writes** — lists are written to a temp file and renamed, so Pi-hole never reads a partial file
- **Slug validation** — only `[a-z0-9-]` allowed, prevents path traversal
- **Safe dashboard rendering** — list slugs and URLs are inserted via `textContent` (not `innerHTML`), preventing XSS if slug validation were ever loosened
- **No key in logs** — gravity trigger logs the Pi-hole base URL only; `PIHOLE_KEY` never appears in the systemd journal
- **systemd hardening** — `ProtectSystem=strict`, `UMask=0022`, dedicated `phlist` user, `ReadWritePaths` locked to list directory

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

## Project structure

```
phlist_server.py        — Server (Flask app, routes, content validation, system stats)
templates/
  dashboard.html        — Web dashboard template
static/
  style.css             — Dashboard styles
  dashboard.js          — Dashboard interactivity
  favicon.svg           — Browser tab icon
tests/
  test_server.py        — 48 tests (auth, CRUD, slug, content validation, dashboard, delete, security headers, gravity-log key-leak, stats, preview)
systemd/
  phlist-server.service — systemd unit for production deployment
scripts/
  update-deployed.sh    — Update an existing /opt/phlist-server systemd deployment
.env.example            — Config template
```
