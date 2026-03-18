"""phlist-server: receive and serve Pi-hole blocklists."""

import hmac
import logging
import os
import re
import unicodedata
from pathlib import Path

from flask import Blueprint, Flask, Response, abort, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── Config ───────────────────────────────────────────────────────────────────
# Read from environment (set via .env loaded by systemd EnvironmentFile=).
# Tests override these with monkeypatch.setattr.
API_KEY: str = os.environ.get("PHLIST_API_KEY", "")
LIST_DIR: Path = Path(os.environ.get("PHLIST_LIST_DIR", "/var/lib/phlist/lists"))
HOST: str = os.environ.get("PHLIST_HOST", "127.0.0.1")
PORT: int = int(os.environ.get("PHLIST_PORT", "8765"))

_MAX_BODY: int = 50 * 1024 * 1024  # 50 MB

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

_MAX_LINE_LEN = 1000
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


# ── Blueprint + rate limiter ──────────────────────────────────────────────────
# The limiter is created here but NOT bound to an app yet.
# create_app() calls limiter.init_app(), which reads RATELIMIT_ENABLED from
# the app config — allowing tests to pass RATELIMIT_ENABLED=False.

limiter = Limiter(get_remote_address, default_limits=[], storage_uri="memory://")
bp = Blueprint("phlist", __name__)


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

    Validates slug format, enforces 50 MB body limit, runs strict content
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
    tmp = LIST_DIR / f"{slug}.txt.tmp"
    dest = LIST_DIR / f"{slug}.txt"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, dest)  # atomic on POSIX — Pi-hole never sees a partial file

    _log.info("PUT /lists/%s.txt — %d bytes written", slug, len(content))
    return Response("ok\n", status=200, content_type="text/plain")


@bp.route("/lists/<slug>.txt", methods=["GET"])
def get_list(slug: str) -> Response:
    """Serve a stored blocklist — no auth required (Pi-hole fetches this)."""
    if not _SLUG_RE.match(slug):
        abort(400)
    path = LIST_DIR / f"{slug}.txt"
    if not path.is_file():
        abort(404)
    return Response(
        path.read_text(encoding="utf-8"),
        status=200,
        content_type="text/plain; charset=utf-8",
    )


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(**config) -> Flask:
    """Create and return a configured Flask app.

    Pass keyword arguments to override Flask config, e.g.:
      create_app(RATELIMIT_ENABLED=False, TESTING=True)
    """
    _app = Flask(__name__)
    _app.config["MAX_CONTENT_LENGTH"] = _MAX_BODY
    _app.config.update(config)
    _app.register_blueprint(bp)
    limiter.init_app(_app)
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
    _log.info("Starting phlist-server on %s:%s", HOST, PORT)
    _log.info("Serving lists from %s", LIST_DIR)
    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()
