"""Test configuration: isolated temp database, offline demo data, no scheduler."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="oik-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'test.db'}"
os.environ["DEMO_MODE"] = "true"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["MC_PATHS"] = "800"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.migrate import upgrade  # noqa: E402

upgrade()


@pytest.fixture()
def db():
    from app.db import SessionLocal
    s = SessionLocal()
    yield s
    s.close()
