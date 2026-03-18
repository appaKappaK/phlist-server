# phlist-server

A lightweight Flask server that receives Pi-hole blocklists pushed from the [phlist](https://github.com/appaKappaK/PiHoleCombineList) desktop app and serves them as plain-text URLs that Pi-hole can subscribe to via gravity.

## How it works

1. You build and push a combined blocklist from the phlist desktop app
2. phlist-server stores it as a `.txt` file on disk
3. Pi-hole fetches the list via a plain HTTP URL
4. Pi-hole runs gravity — done

## API

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | GET | Bearer token | Connection test |
| `/lists/{slug}.txt` | PUT | Bearer token | Receive & store a blocklist |
| `/lists/{slug}.txt` | GET | None | Serve list to Pi-hole |

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
python3 -m venv /opt/phlist-server/venv
/opt/phlist-server/venv/bin/pip install flask flask-limiter
```

### 2. Create config

```bash
sudo mkdir -p /etc/phlist-server
sudo cp .env.example /etc/phlist-server/.env
sudo nano /etc/phlist-server/.env
# Set PHLIST_API_KEY and PHLIST_HOST (your Tailscale IP)
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
| `PHLIST_HOST` | `127.0.0.1` | IP address to bind to — set to your Tailscale IP (`100.x.y.z`) |
| `PHLIST_PORT` | `8765` | TCP port |

## Network

The server is designed to bind to your **Tailscale IP** so all traffic (LAN and WAN) goes through the encrypted WireGuard tunnel.

- LAN peers connect directly — no relay, negligible overhead
- Pi-hole subscribes via MagicDNS: `http://orangepi.your-tailnet.ts.net:8765/lists/slug.txt`
- No TLS needed at the Flask level — Tailscale handles encryption

## Security

- **Bearer token auth** with constant-time comparison (`hmac.compare_digest`) — prevents timing attacks
- **Strict content validation** — every uploaded line must be ASCII-only and match a known blocklist format; non-ASCII characters (Unicode homoglyphs, zero-width chars, bidi overrides) are rejected with a detailed error showing which line failed
- **Rate limiting** — 10 req/min on health check, 5 req/min on PUT uploads
- **Atomic writes** — lists are written to a temp file and renamed, so Pi-hole never reads a partial file
- **Slug validation** — only `[a-z0-9-]` allowed, prevents path traversal
- **systemd hardening** — `ProtectSystem=strict`, dedicated `phlist` user, `ReadWritePaths` locked to list directory

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

## Project structure

```
phlist_server.py        — Single-file server (~160 lines)
tests/
  test_server.py        — 27 tests (auth, CRUD, slug, content validation)
systemd/
  phlist-server.service — systemd unit for production deployment
.env.example            — Config template
```
