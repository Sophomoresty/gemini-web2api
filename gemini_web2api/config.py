"""Configuration management."""
import json
import os

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "0.0.0.0",
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "gemini_bl": "boq_assistant-bard-web-server_20260525.09_p0",
    "default_model": "gemini-3.5-flash",
    "log_requests": True,
    "cookie_file": None,
    "proxy": None,
    "api_keys": [],
    "web_username": "admin",
    "web_password": "admin",
    "session_secret": None,
    "session_cookie": "gemini2api_session",
    "session_ttl_sec": 86400,
    "session_stats_file": None,
}

CONFIG = dict(DEFAULT_CONFIG)
CONFIG_PATH = None


def load_config(path: str = None):
    """Load config from JSON file."""
    global CONFIG_PATH
    if path and os.path.exists(path):
        CONFIG_PATH = os.path.abspath(path)
        with open(path) as f:
            CONFIG.update(json.load(f))
    return CONFIG


def api_keys_list() -> list:
    """Return normalized API keys while preserving order."""
    keys = CONFIG.get("api_keys") or []
    if isinstance(keys, str):
        keys = [keys]
    result = []
    seen = set()
    for key in keys:
        value = str(key).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def validate_api_key_value(value: str) -> str:
    """Validate and normalize an API key value."""
    value = str(value or "").strip()
    if not value:
        raise ValueError("API key cannot be empty")
    if len(value) < 8:
        raise ValueError("API key must be at least 8 characters")
    if any(ch.isspace() for ch in value):
        raise ValueError("API key cannot contain whitespace")
    return value


def persist_config():
    """Persist current CONFIG back to the loaded config file atomically."""
    global CONFIG_PATH
    if not CONFIG_PATH:
        CONFIG_PATH = os.path.abspath("./config.json")
    path = os.path.abspath(CONFIG_PATH)
    current = {}
    if os.path.exists(path):
        with open(path, "r") as f:
            current = json.load(f)
    current.update(CONFIG)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    os.chmod(tmp_path, mode)
    os.replace(tmp_path, path)


def find_config():
    """Search for config file in standard locations."""
    for p in ["./config.json", os.path.expanduser("~/.config/gemini-web2api/config.json")]:
        if os.path.exists(p):
            return p
    return None
