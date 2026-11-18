import pytest


@pytest.fixture(autouse=True)
def fake_rapidapi_key(monkeypatch):
    """Give every test a fake RapidAPI key.

    Tests must not depend on the real key in .env (CI has no .env), and must never send it anywhere.
    """
    monkeypatch.setenv("RAPIDAPI_KEY", "test-rapidapi-key")
