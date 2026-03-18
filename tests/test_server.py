"""Tests for phlist-server."""

import pytest
import phlist_server


TEST_KEY = "test-secret-key"
AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
WRONG_AUTH = {"Authorization": "Bearer wrong-key"}
PUT_HEADERS = {**AUTH, "Content-Type": "text/plain; charset=utf-8"}

VALID_LIST = """\
# phlist combined blocklist
ads.example.com
tracker.evil.com
0.0.0.0 malware.bad.com
127.0.0.1 telemetry.corp.net
||adservice.google.com^
||doubleclick.net^$third-party
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(phlist_server, "API_KEY", TEST_KEY)
    monkeypatch.setattr(phlist_server, "LIST_DIR", tmp_path)
    # create_app passes RATELIMIT_ENABLED=False so rate limits don't
    # accumulate across tests (limiter reads config at init time).
    test_app = phlist_server.create_app(RATELIMIT_ENABLED=False, TESTING=True)
    with test_app.test_client() as c:
        yield c


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_ok(client):
    resp = client.get("/health", headers=AUTH)
    assert resp.status_code == 200
    assert resp.data == b"ok\n"


def test_health_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 401


def test_health_wrong_key(client):
    resp = client.get("/health", headers=WRONG_AUTH)
    assert resp.status_code == 403


# ── PUT /lists/<slug>.txt — auth ──────────────────────────────────────────────

def test_put_no_auth(client):
    resp = client.put("/lists/my-list.txt", data="ads.example.com\n")
    assert resp.status_code == 401


def test_put_wrong_auth(client):
    resp = client.put("/lists/my-list.txt", headers=WRONG_AUTH, data="ads.example.com\n")
    assert resp.status_code == 403


# ── PUT /lists/<slug>.txt — CRUD ─────────────────────────────────────────────

def test_put_creates_file(client, tmp_path):
    resp = client.put("/lists/my-list.txt", headers=PUT_HEADERS, data=VALID_LIST)
    assert resp.status_code == 200
    assert (tmp_path / "my-list.txt").is_file()


def test_put_content_matches(client, tmp_path):
    resp = client.put("/lists/blocklist.txt", headers=PUT_HEADERS, data=VALID_LIST)
    assert resp.status_code == 200
    assert (tmp_path / "blocklist.txt").read_text() == VALID_LIST


def test_put_overwrites_existing(client, tmp_path):
    client.put("/lists/my-list.txt", headers=PUT_HEADERS, data="ads.example.com\n")
    client.put("/lists/my-list.txt", headers=PUT_HEADERS, data="tracker.bad.com\n")
    assert (tmp_path / "my-list.txt").read_text() == "tracker.bad.com\n"


def test_put_no_tmp_left_behind(client, tmp_path):
    client.put("/lists/my-list.txt", headers=PUT_HEADERS, data=VALID_LIST)
    assert not (tmp_path / "my-list.txt.tmp").exists()


# ── GET /lists/<slug>.txt ─────────────────────────────────────────────────────

def test_get_no_auth_required(client, tmp_path):
    (tmp_path / "my-list.txt").write_text(VALID_LIST)
    resp = client.get("/lists/my-list.txt")
    assert resp.status_code == 200


def test_get_returns_content(client, tmp_path):
    (tmp_path / "my-list.txt").write_text(VALID_LIST)
    resp = client.get("/lists/my-list.txt")
    assert resp.data.decode() == VALID_LIST


def test_get_not_found(client):
    resp = client.get("/lists/does-not-exist.txt")
    assert resp.status_code == 404


# ── Slug validation ───────────────────────────────────────────────────────────

def test_slug_rejects_uppercase(client):
    resp = client.put("/lists/MyList.txt", headers=PUT_HEADERS, data="ads.example.com\n")
    # Flask won't even route this (URL won't match slug pattern), expect 404 or 400
    assert resp.status_code in (400, 404)


def test_slug_single_char(client, tmp_path):
    resp = client.put("/lists/a.txt", headers=PUT_HEADERS, data="ads.example.com\n")
    assert resp.status_code == 200
    assert (tmp_path / "a.txt").is_file()


def test_slug_valid_with_numbers(client, tmp_path):
    resp = client.put("/lists/list-01.txt", headers=PUT_HEADERS, data="ads.example.com\n")
    assert resp.status_code == 200


# ── Content validation — valid formats ───────────────────────────────────────

def test_content_plain_domains(client):
    data = "ads.example.com\ntracker.bad.com\n"
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 200


def test_content_hosts_format(client):
    data = "0.0.0.0 ads.example.com\n127.0.0.1 tracker.bad.com\n"
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 200


def test_content_abp_format(client):
    data = "||ads.example.com^\n||tracker.bad.com^$third-party\n@@||safe.example.com^\n"
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 200


def test_content_comments_and_blanks(client):
    data = "# This is a comment\n! Also a comment\n\nads.example.com\n"
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 200


def test_content_ip_standalone(client):
    data = "0.0.0.0\n127.0.0.1\n"
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 200


# ── Content validation — rejected content ────────────────────────────────────

def test_content_rejects_non_ascii(client):
    # Cyrillic 'а' (U+0430) looks identical to Latin 'a' — classic homoglyph attack
    data = "аds.example.com\n"  # first char is Cyrillic
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 400
    body = resp.data.decode()
    assert "U+0430" in body
    assert "Line 1" in body


def test_content_rejects_zero_width(client):
    data = "ads\u200b.example.com\n"  # zero-width space
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 400
    assert "U+200B" in resp.data.decode()


def test_content_rejects_bidi_override(client):
    data = "ads.example.com\u202e\n"  # RIGHT-TO-LEFT OVERRIDE
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 400
    assert "U+202E" in resp.data.decode()


def test_content_rejects_unrecognised_format(client):
    data = "eval(base64_decode(malicious))\n"
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 400
    body = resp.data.decode()
    assert "unrecognised format" in body
    assert "Line 1" in body


def test_content_rejects_line_too_long(client):
    data = "a" * 1001 + "\n"
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 400
    assert "too long" in resp.data.decode()


def test_content_error_shows_line_number(client):
    # Put a bad line on line 3
    data = "ads.example.com\ntracker.bad.com\neval(bad)\n"
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 400
    assert "Line 3" in resp.data.decode()


def test_content_reports_up_to_10_violations(client):
    # 12 bad lines — should report max 10 + omitted notice
    bad_line = "eval(bad)\n"
    data = bad_line * 12
    resp = client.put("/lists/t.txt", headers=PUT_HEADERS, data=data)
    assert resp.status_code == 400
    body = resp.data.decode()
    assert "further violations omitted" in body
