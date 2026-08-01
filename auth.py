#!/usr/bin/env python3
"""
Lightweight local accounts for Autonom.

Prototype-grade auth for a personal/local app — NOT production security. Users
are stored in config/users.json (git-ignored) with a per-user random salt and a
PBKDF2-HMAC-SHA256 password hash (200k iterations). No plaintext passwords are
ever stored. For a public deployment you'd move to a real identity provider,
HTTPS, cookie/session management (e.g. streamlit-authenticator), rate limiting.
"""
import hashlib
import json
import os
import re
import secrets
import unicodedata

USERS_PATH = "config/users.json"
_ITERATIONS = 200_000


def _load() -> dict:
    if os.path.exists(USERS_PATH):
        with open(USERS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def normalize_username(name: str) -> str:
    return (name or "").strip().lower()


def safe_key(name: str) -> str:
    """Filesystem-safe key for a username (used for per-user data paths)."""
    n = unicodedata.normalize("NFKD", normalize_username(name))
    return re.sub(r"[^a-z0-9_-]+", "_", n) or "user"


def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt_hex), _ITERATIONS).hex()


def user_exists(username: str) -> bool:
    return normalize_username(username) in _load()


def create_user(username: str, password: str, display: str = "") -> tuple[bool, str]:
    u = normalize_username(username)
    if not re.fullmatch(r"[a-z0-9_.-]{3,30}", u):
        return False, "Username: 3–30 chars, letters/numbers/._- only."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    users = _load()
    if u in users:
        return False, "That username is taken."
    salt = secrets.token_hex(16)
    users[u] = {"salt": salt, "hash": _hash(password, salt),
                "display": (display.strip() or username.strip())}
    _save(users)
    return True, "Account created."


def verify(username: str, password: str) -> bool:
    users = _load()
    rec = users.get(normalize_username(username))
    if not rec:
        return False
    return secrets.compare_digest(rec["hash"], _hash(password, rec["salt"]))


def display_name(username: str) -> str:
    rec = _load().get(normalize_username(username))
    return rec.get("display", username) if rec else username


def count() -> int:
    return len(_load())
