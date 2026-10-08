"""Configuration. Secrets never live in source code."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
IS_VERCEL = bool(os.environ.get("VERCEL"))
INSTANCE_DIR = Path(os.environ.get("INSTANCE_DIR", "/tmp/school-erp" if IS_VERCEL else BASE_DIR / "instance"))


def load_secret_key() -> str:
    """Use the configured key; persist a generated key only on writable local installs."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    if IS_VERCEL:
        raise RuntimeError("Set SECRET_KEY in Vercel Project Settings → Environment Variables.")
    INSTANCE_DIR.mkdir(mode=0o700, exist_ok=True)
    key_file = INSTANCE_DIR / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key)
    return key
