import pytest

from app.confluence.client import ConfluenceClient, ConfluenceConfigError
from app.settings import Settings


def test_client_raises_when_base_url_missing():
    settings = Settings(
        CONFLUENCE_BASE_URL=None, CONFLUENCE_USERNAME="u", CONFLUENCE_PASSWORD="p"
    )
    with pytest.raises(ConfluenceConfigError):
        ConfluenceClient(settings)


def test_client_raises_when_username_missing():
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME=None,
        CONFLUENCE_PASSWORD="p",
    )
    with pytest.raises(ConfluenceConfigError):
        ConfluenceClient(settings)


def test_client_raises_when_password_missing():
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com",
        CONFLUENCE_USERNAME="u",
        CONFLUENCE_PASSWORD=None,
    )
    with pytest.raises(ConfluenceConfigError):
        ConfluenceClient(settings)


def test_client_constructs_with_all_set():
    settings = Settings(
        CONFLUENCE_BASE_URL="https://c.example.com/",
        CONFLUENCE_USERNAME="svc-citec-kb",
        CONFLUENCE_PASSWORD="secret-pw",
    )
    client = ConfluenceClient(settings)
    assert client._base_url == "https://c.example.com"
    assert client._auth == ("svc-citec-kb", "secret-pw")
