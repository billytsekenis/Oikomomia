"""Run Alembic migrations programmatically at start-up."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import BACKEND_DIR


def upgrade() -> None:
    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "alembic"))
    command.upgrade(cfg, "head")
