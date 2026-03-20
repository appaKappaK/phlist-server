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
| `/lists/{slug}.txt` | DELETE | Bearer token | Delete a stored list |

## Quick start (local / dev)

```bash
git clone https://github.com/appaKappaK/phlist-server.git
cd phlist-server
pip install -r requirements.txt

cp .env.example .env
# Edit .env — set PHLIST_API_KEY and PHLIST_HOST

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
# Set PHLIST_HOST=0.0.0.0 if Pi-hole is on the LAN (not Tailscale)
# Set PHLIST_HOST=100.x.y.z to restrict to Tailscale peers only
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

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PHLIST_API_KEY` | *(required)* | Bearer token for authentication. Generate with: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `PHLIST_LIST_DIR` | `/var/lib/phlist/lists` | Directory where blocklist `.txt` files are stored |
| `PHLIST_HOST` | `0.0.0.0` | IP address to bind to. Use `0.0.0.0` to listen on all interfaces (required if Pi-hole is not on Tailscale). Use your Tailscale IP (`100.x.y.z`) to restrict to Tailscale peers only. |
| `PHLIST_PORT` | `8765` | TCP port |
| `PHLIST_PIHOLE_URL` | *(unset)* | Optional: Pi-hole base URL for auto-gravity trigger after each push (e.g. `http://pi.hole`) |
| `PHLIST_PIHOLE_KEY` | *(unset)* | Optional: Pi-hole API key used with `PHLIST_PIHOLE_URL` |

## Network

Two common setups:

**All-interfaces (recommended for LAN setups):** Set `PHLIST_HOST=0.0.0.0`. Pi-hole subscribes via your server's LAN IP:
```
http://.PUT.IP.HERE:8765/lists/slug.txt
```

**Tailscale-only:** Set `PHLIST_HOST` to your Tailscale IP (`100.x.y.z`). Pi-hole subscribes via MagicDNS:
```
http://orangepi.your-tailnet.ts.net:8765/lists/slug.txt
```
No TLS needed at the Flask level — Tailscale handles encryption end-to-end.

**LAN with HTTPS (optional):** If you're not on Tailscale and want the phlist client → server push encrypted, put [Caddy](https://caddyserver.com) in front. Caddy generates and manages a local TLS certificate automatically.

Install Caddy on the server (Debian/Ubuntu/Orange Pi):
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

Create `/etc/caddy/Caddyfile`:
```
.PUT.IP.HERE:8766 {
    tls internal
    reverse_proxy localhost:8765
}
```

Then `sudo systemctl reload caddy`. Point the phlist client at `https://.PUT.IP.HERE:8766`.

Pi-hole gravity stays on plain HTTP — the list URLs require no authentication, so there is nothing sensitive in that traffic:
```
http://.PUT.IP.HERE:8765/lists/slug.txt
```

**Cert trust:** `tls internal` creates a local CA. After first run, export it and add it to your OS trust store on the machine running the phlist desktop client:
```bash
# On the server — get the CA cert
sudo cat /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
```
Save the output as `caddy-local-ca.crt`, then on the client machine:
```bash
# Fedora/RHEL
sudo cp caddy-local-ca.crt /etc/pki/ca-trust/source/anchors/ && sudo update-ca-trust

# Ubuntu/Debian
sudo cp caddy-local-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates
```
After trusting the cert, update the phlist client Server URL to `https://.PUT.IP.HERE:8766` and re-test the connection.

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
- **Optional HTTPS** — for non-Tailscale LAN deployments, a Caddy reverse proxy adds TLS with a single `tls internal` directive; Pi-hole gravity continues over plain HTTP (no auth required on list URLs)

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
  test_server.py        — 43 tests (auth, CRUD, slug, content validation, dashboard, delete, security headers, gravity-log key-leak)
systemd/
  phlist-server.service — systemd unit for production deployment
.env.example            — Config template
```
