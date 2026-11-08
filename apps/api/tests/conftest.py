import pytest

from app.config import settings
from app.store import store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    db_path = tmp_path / "test_sessions.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(settings, "mock_browser", True)
    store.reset(settings.database_url)
    yield
    store.reset("memory")
