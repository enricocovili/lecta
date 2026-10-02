"""Internal secrets, generated on first boot and stored in the data volume.

`DATA_DIR/secrets/secrets.json` holds:
  * fernet_key  – encrypts provider API keys and TOTP secrets at rest
  * setup_code  – one-time code required by the setup wizard

The file is created atomically with mode 0600. Both the backend and the worker
call `load()`; whoever comes first creates it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets as pysecrets
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import config

log = logging.getLogger(__name__)


def _secrets_dir() -> Path:
    return config.data_dir / "secrets"


def _generate() -> dict:
    return {
        "fernet_key": Fernet.generate_key().decode(),
        "setup_code": "-".join(pysecrets.token_hex(3) for _ in range(3)),
    }


@lru_cache(maxsize=1)
def load() -> dict:
    d = _secrets_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "secrets.json"
    if not path.exists():
        data = _generate()
        tmp = d / f".secrets.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        try:
            # link() fails if another process won the race; keep theirs.
            os.link(tmp, path)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    data = json.loads(path.read_text())
    (d / "setup-code").write_text(data["setup_code"] + "\n")
    return data


def fernet() -> Fernet:
    return Fernet(load()["fernet_key"].encode())


def encrypt(value: str) -> str:
    return fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("could not decrypt a stored secret (fernet key changed?)")
        return None


def setup_code() -> str:
    return load()["setup_code"]
