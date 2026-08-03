from app.settings import Settings, get_settings


def test_confluence_settings_default_to_none(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_BASE_URL", raising=False)
    monkeypatch.delenv("CONFLUENCE_USERNAME", raising=False)
    monkeypatch.delenv("CONFLUENCE_PASSWORD", raising=False)
    s = Settings()
    assert s.confluence_base_url is None
    assert s.confluence_username is None
    assert s.confluence_password is None


def test_confluence_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://confluence.internal.example.com")
    monkeypatch.setenv("CONFLUENCE_USERNAME", "svc-citec-kb")
    monkeypatch.setenv("CONFLUENCE_PASSWORD", "secret-pw")
    get_settings.cache_clear()
    s = Settings()
    assert s.confluence_base_url == "https://confluence.internal.example.com"
    assert s.confluence_username == "svc-citec-kb"
    assert s.confluence_password == "secret-pw"
    get_settings.cache_clear()
