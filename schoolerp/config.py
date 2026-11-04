"""Configuration. Secrets never live in source code."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"


def load_secret_key() -> str:
    """Use $SECRET_KEY if set, else create a random key once, stored 0600 in instance/."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    INSTANCE_DIR.mkdir(mode=0o700, exist_ok=True)
    key_file = INSTANCE_DIR / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key)
    return key
