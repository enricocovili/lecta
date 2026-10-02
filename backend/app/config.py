"""Process-level configuration.

Everything here is optional and has a sane default for the compose stack.
Application settings (providers, prompts, limits, ...) are NOT configured here:
they live in the database and are edited from the web UI (see services/settings.py).
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


class Config:
    # Postgres. The password is generated on first boot by the db container and
    # shared through the `secrets` volume; an explicit URL overrides everything.
    database_url_override: str = _env("LECTA_DATABASE_URL", "")
    db_host: str = _env("LECTA_DB_HOST", "db")
    db_name: str = _env("LECTA_DB_NAME", "lecta")
    db_user: str = _env("LECTA_DB_USER", "lecta")
    db_password_file: Path = Path(_env("LECTA_DB_PASSWORD_FILE", "/secrets/db-password"))

    data_dir: Path = Path(_env("LECTA_DATA_DIR", "/data"))
    # Mount point of the volume shared with the compile container, and the
    # directory (inside it) where this instance keeps project build dirs.
    latex_root: Path = Path(_env("LECTA_LATEX_ROOT", "/latex-work"))
    latex_work_dir: Path = Path(_env("LECTA_LATEX_WORK", "/latex-work"))
    latex_socket: str = _env("LECTA_LATEX_SOCKET", "/run/lecta/latex.sock")
    model_dir: Path = Path(_env("LECTA_MODEL_DIR", "/opt/models"))

    # auto: Secure cookie when the request arrived over HTTPS (X-Forwarded-Proto).
    cookie_secure: str = _env("LECTA_COOKIE_SECURE", "auto")  # auto | always | never
    log_level: str = _env("LECTA_LOG_LEVEL", "INFO")
    # Published courses are rebuilt after this many seconds without changes.
    republish_quiet_s: int = int(_env("LECTA_REPUBLISH_QUIET_S", "120"))

    @classmethod
    def database_url(cls) -> str:
        if cls.database_url_override:
            return cls.database_url_override
        password = "lecta"
        try:
            password = cls.db_password_file.read_text().strip() or password
        except OSError:
            pass
        return f"postgresql+asyncpg://{cls.db_user}:{password}@{cls.db_host}:5432/{cls.db_name}"


config = Config()
