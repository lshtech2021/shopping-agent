import os

# Force in-memory store for unit tests (before app settings are imported).
os.environ["SA_DATABASE_URL"] = "memory"

import pytest

from app.config import settings
from app.store import store


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "memory")
    monkeypatch.setattr(settings, "mock_browser", True)
    store.reset("memory")
    yield
    store.reset("memory")
