"""phlist-server: receive and serve Pi-hole blocklists."""

import hmac
import json
import logging
import os
import re
import shutil
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from flask import Blueprint, Flask, Response, abort, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── Optional dotenv ───────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv("/etc/phlist-server/.env")  # production path
    load_dotenv()                           # local .env override
except ImportError:
    pass  # optional; systemd EnvironmentFile= handles production

# ── Config ───────────────────────────────────────────────────────────────────
# Read from environment (set via .env loaded by systemd EnvironmentFile=).
# Tests override these with monkeypatch.setattr.
API_KEY:    str  = os.environ.get("PHLIST_API_KEY", "")
LIST_DIR:   Path = Path(os.environ.get("PHLIST_LIST_DIR", "/var/lib/phlist/lists"))
HOST:       str  = os.environ.get("PHLIST_HOST", "127.0.0.1")
PORT:       int  = int(os.environ.get("PHLIST_PORT", "8765"))
PIHOLE_URL: str  = os.environ.get("PHLIST_PIHOLE_URL", "")
PIHOLE_KEY: str  = os.environ.get("PHLIST_PIHOLE_KEY", "")


def _resolve_display_host(host: str) -> str:
    """When bound to 0.0.0.0, find the real outbound LAN IP for display."""
    if host != "0.0.0.0":
        return host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return host


DISPLAY_HOST = _resolve_display_host(HOST)

_MAX_BODY = 2 * 1024 * 1024 * 1024  # 2 GB
_VERSION  = "1.3.0"

_log = logging.getLogger("phlist-server")

# ── Validation patterns ───────────────────────────────────────────────────────
# Slug: lowercase alphanumeric + hyphens, matches what phlist's _slugify() produces.
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# Valid blocklist line formats (applied after the non-ASCII scan).
_LINE_RE = re.compile(
    r"""
    ^$                                                # blank line
  | ^\s*[#!]                                         # comment (# or !)
  | ^[a-zA-Z0-9][a-zA-Z0-9.\-]*$                    # plain domain / hostname
  | ^[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?:\s+\S+)?$     # IP address or hosts-format entry
  | ^(?:@@)?\|\|[a-zA-Z0-9][a-zA-Z0-9.\-]*\^        # ABP block (||) or allow (@@||) rule
  | ^/.*/$                                           # regex filter
  | ^\[.*\]$                                         # section header e.g. [Adblock Plus 2.0]
    """,
    re.VERBOSE,
)

_MAX_LINE_LEN  = 1000
_MAX_VIOLATIONS = 10


def _validate_content(text: str) -> tuple[bool, str]:
    """Strict line-by-line validation of uploaded blocklist content.

    Returns ``(True, "")`` on success.
    Returns ``(False, error_message)`` with up to 10 annotated violations.

    Rejects:
    - Any non-ASCII characters (catches Unicode homoglyphs, zero-width chars,
      bidirectional override characters, and any other invisible/obfuscated Unicode)
    - Lines exceeding 1000 characters
    - Lines that don't match a known blocklist format
    """
    violations: list[str] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(violations) >= _MAX_VIOLATIONS:
            violations.append("  (further violations omitted)")
            break

        # Step 1: non-ASCII scan — catches all Unicode attack vectors.
        for col, ch in enumerate(line, start=1):
            code = ord(ch)
            if code > 0x7E or (code < 0x20 and ch not in "\t"):
                name = unicodedata.name(ch, f"U+{code:04X}")
                violations.append(
                    f"  Line {lineno}, col {col}: non-ASCII character"
                    f" U+{code:04X} ({name})"
                )
                break  # one violation reported per line
        else:
            stripped = line.strip()

            # Step 2: line length check.
            if len(line) > _MAX_LINE_LEN:
                violations.append(
                    f"  Line {lineno}: line too long"
                    f" ({len(line)} chars, max {_MAX_LINE_LEN})"
                )

            # Step 3: format check.
            elif not _LINE_RE.match(stripped):
                preview = stripped[:80] + ("..." if len(stripped) > 80 else "")
                violations.append(
                    f"  Line {lineno}: unrecognised format: {preview!r}"
                )

    if violations:
        return False, "Content validation failed:\n" + "\n".join(violations)
    return True, ""


# ── Auth helper ───────────────────────────────────────────────────────────────

def _require_auth() -> None:
    """Abort 401/403 if the request lacks a valid Bearer token.

    Uses ``hmac.compare_digest`` to prevent timing-based key enumeration.
    """
    if not API_KEY:
        _log.error("PHLIST_API_KEY is not configured — rejecting request")
        abort(500, "Server has no API key configured")
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        abort(401)
    token = header[7:]
    if not hmac.compare_digest(token.encode("utf-8"), API_KEY.encode("utf-8")):
        abort(403)


# ── List scanner ──────────────────────────────────────────────────────────────

def _scan_lists() -> list[dict]:
    """Return metadata for all stored lists, sorted by name."""
    if not LIST_DIR.is_dir():
        return []
    result = []
    for p in sorted(LIST_DIR.glob("*.txt")):
        stat = p.stat()
        try:
            lines = sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
        except OSError:
            lines = 0
        result.append({
            "slug":  p.stem,
            "size":  stat.st_size,
            "lines": lines,
            "mtime": stat.st_mtime,
        })
    return result


# ── Gravity trigger ───────────────────────────────────────────────────────────

def _trigger_gravity() -> None:
    """Fire-and-forget Pi-hole gravity update — no-op when PHLIST_PIHOLE_URL is unset."""
    if not PIHOLE_URL:
        return

    def _fire() -> None:
        try:
            url = f"{PIHOLE_URL.rstrip('/')}/admin/api.php?gravity&auth={PIHOLE_KEY}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                _log.info("Gravity trigger: %s → %s", PIHOLE_URL, resp.status)
        except Exception as exc:
            _log.warning("Gravity trigger failed: %s", exc)

    threading.Thread(target=_fire, daemon=True).start()


# ── System stats ─────────────────────────────────────────────────────────────

def _get_system_stats() -> dict:
    """Read system metrics from /proc and /sys — stdlib only, no psutil."""
    stats: dict = {}

    # Hostname
    try:
        stats["hostname"] = socket.gethostname()
    except Exception:
        pass

    # CPU % (two /proc/stat snapshots 200ms apart)
    try:
        def _read_cpu():
            line = Path("/proc/stat").open().readline()
            vals = list(map(int, line.split()[1:]))
            idle = vals[3] + vals[4] if len(vals) > 4 else vals[3]
            total = sum(vals)
            return idle, total

        idle1, total1 = _read_cpu()
        time.sleep(0.2)
        idle2, total2 = _read_cpu()
        d_total = total2 - total1
        d_idle  = idle2 - idle1
        stats["cpu_pct"] = round((1 - d_idle / d_total) * 100, 1) if d_total else 0.0
    except Exception:
        pass

    # CPU temperature (°C)
    for zone in range(5):
        p = Path(f"/sys/class/thermal/thermal_zone{zone}/temp")
        try:
            stats["cpu_temp_c"] = round(int(p.read_text().strip()) / 1000, 1)
            break
        except Exception:
            continue

    # RAM (MB)
    try:
        mem: dict = {}
        for line in Path("/proc/meminfo").open():
            k, v = line.split(":", 1)
            mem[k.strip()] = int(v.split()[0])  # kB
        total_mb = mem["MemTotal"] // 1024
        avail_mb = mem.get("MemAvailable", mem.get("MemFree", 0)) // 1024
        stats["mem_total_mb"] = total_mb
        stats["mem_used_mb"]  = total_mb - avail_mb
        stats["mem_pct"]      = round((total_mb - avail_mb) / total_mb * 100, 1) if total_mb else 0.0
    except Exception:
        pass

    # Disk
    try:
        du = shutil.disk_usage(LIST_DIR if LIST_DIR.is_dir() else Path("/"))
        stats["disk_total_gb"] = round(du.total / 1024 ** 3, 1)
        stats["disk_used_gb"]  = round(du.used  / 1024 ** 3, 1)
        stats["disk_pct"]      = round(du.used / du.total * 100, 1)
    except Exception:
        pass

    # Uptime (seconds)
    try:
        stats["uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        pass

    # Load average
    try:
        la = os.getloadavg()
        stats["load_avg"] = [round(x, 2) for x in la]
    except Exception:
        pass

    return stats


# ── Blueprint + rate limiter ──────────────────────────────────────────────────
# The limiter is created here but NOT bound to an app yet.
# create_app() calls limiter.init_app(), which reads RATELIMIT_ENABLED from
# the app config — allowing tests to pass RATELIMIT_ENABLED=False.

limiter = Limiter(get_remote_address, default_limits=[], storage_uri="memory://")
bp = Blueprint("phlist", __name__)


@bp.route("/", methods=["GET"])
@limiter.limit("30 per minute")
def dashboard() -> Response:
    """Web dashboard showing all stored lists."""
    lists = _scan_lists()
    disk_pct = None
    try:
        du = shutil.disk_usage(LIST_DIR if LIST_DIR.is_dir() else Path("/"))
        disk_pct = round(du.used / du.total * 100, 1)
    except OSError:
        pass
    return render_template(
        "dashboard.html",
        lists=lists,
        disk_pct=disk_pct,
        host=DISPLAY_HOST,
        port=PORT,
        version=_VERSION,
    )


@bp.route("/lists/", methods=["GET"])
@limiter.limit("30 per minute")
def list_inventory() -> Response:
    """JSON inventory of all stored lists."""
    return Response(
        json.dumps(_scan_lists()),
        status=200,
        content_type="application/json",
    )


@bp.route("/api/stats", methods=["GET"])
@limiter.limit("30 per minute")
def api_stats() -> Response:
    """System stats for the dashboard sidebar — no auth required."""
    return Response(
        json.dumps(_get_system_stats()),
        status=200,
        content_type="application/json",
    )


@bp.route("/health", methods=["GET"])
@limiter.limit("10 per minute")
def health() -> Response:
    """Connection test — requires Bearer auth."""
    _require_auth()
    return Response("ok\n", status=200, content_type="text/plain")


@bp.route("/lists/<slug>.txt", methods=["PUT"])
@limiter.limit("5 per minute")
def put_list(slug: str) -> Response:
    """Receive and store a blocklist — requires Bearer auth.

    Validates slug format, enforces 2 GB body limit, runs strict content
    validation, then atomically writes the file to LIST_DIR.
    """
    _require_auth()

    if not _SLUG_RE.match(slug):
        return Response(
            f"Invalid slug: {slug!r}\n", status=400, content_type="text/plain"
        )

    content = request.get_data(as_text=True)

    ok, err = _validate_content(content)
    if not ok:
        _log.warning("PUT /lists/%s.txt rejected: content validation failed", slug)
        return Response(err + "\n", status=400, content_type="text/plain")

    LIST_DIR.mkdir(parents=True, exist_ok=True)
    tmp  = LIST_DIR / f"{slug}.txt.tmp"
    dest = LIST_DIR / f"{slug}.txt"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, dest)  # atomic on POSIX — Pi-hole never sees a partial file

    _log.info("PUT /lists/%s.txt — %d bytes written", slug, len(content))
    _trigger_gravity()
    return Response("ok\n", status=200, content_type="text/plain")


@bp.route("/lists/<slug>.txt", methods=["GET"])
def get_list(slug: str) -> Response:
    """Serve a stored blocklist — no auth required (Pi-hole fetches this).

    Add ``?preview=1`` to get only the first 100 lines without loading the full file.
    """
    if not _SLUG_RE.match(slug):
        abort(400)
    path = LIST_DIR / f"{slug}.txt"
    if not path.is_file():
        abort(404)
    if request.args.get("preview"):
        lines = []
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i >= 100:
                        break
                    lines.append(line)
        except OSError:
            abort(500)
        return Response("".join(lines), status=200, content_type="text/plain; charset=utf-8")
    return Response(
        path.read_text(encoding="utf-8"),
        status=200,
        content_type="text/plain; charset=utf-8",
    )


@bp.route("/lists/<slug>.txt", methods=["DELETE"])
@limiter.limit("10 per minute")
def delete_list(slug: str) -> Response:
    """Delete a stored list — requires Bearer auth."""
    _require_auth()
    if not _SLUG_RE.match(slug):
        abort(400)
    path = LIST_DIR / f"{slug}.txt"
    if not path.is_file():
        abort(404)
    path.unlink()
    _log.info("DELETE /lists/%s.txt", slug)
    return Response("ok\n", status=200, content_type="text/plain")


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(**config) -> Flask:
    """Create and return a configured Flask app.

    Pass keyword arguments to override Flask config, e.g.:
      create_app(RATELIMIT_ENABLED=False, TESTING=True)
    """
    _app = Flask(__name__, template_folder="templates", static_folder="static")
    _app.config["MAX_CONTENT_LENGTH"] = _MAX_BODY
    _app.config.update(config)
    _app.register_blueprint(bp)
    limiter.init_app(_app)

    @_app.after_request
    def _security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'",
        )
        return response

    return _app


# Production app instance (used when running the server directly).
app = create_app()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not API_KEY:
        _log.error("PHLIST_API_KEY is not set — refusing to start")
        raise SystemExit(1)
    LIST_DIR.mkdir(parents=True, exist_ok=True)
    _log.info("Starting phlist-server v%s on %s:%s", _VERSION, HOST, PORT)
    _log.info("Serving lists from %s", LIST_DIR)
    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()
