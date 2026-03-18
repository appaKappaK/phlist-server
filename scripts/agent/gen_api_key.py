"""Generate a secure random API key for PHLIST_API_KEY.

Usage:
    python scripts/agent/gen_api_key.py
"""
import secrets

key = secrets.token_hex(32)
print(key)
