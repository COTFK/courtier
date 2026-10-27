from unittest.mock import MagicMock

import pytest
from obstore.store import S3Store

from courtier.providers.bucket import _bucket_endpoint, create_bucket_client
from courtier.settings import SETTINGS


def test_path_style_keeps_the_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SETTINGS, "s3_addressing_style", "path")

    assert _bucket_endpoint() == "https://s3.example.test"


def test_virtual_style_puts_the_bucket_in_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SETTINGS, "s3_addressing_style", "virtual")

    assert _bucket_endpoint() == "https://test-bucket.s3.example.test"


@pytest.mark.parametrize("style", ["path", "virtual"])
def test_client_is_built_for_either_addressing_style(
    monkeypatch: pytest.MonkeyPatch, style: str
) -> None:
    monkeypatch.setattr(SETTINGS, "s3_addressing_style", style)

    assert isinstance(create_bucket_client(), S3Store)


@pytest.mark.parametrize("allow_http", [False, True])
def test_client_http_access_is_explicitly_configured(
    monkeypatch: pytest.MonkeyPatch, *, allow_http: bool
) -> None:
    create_store = MagicMock()
    monkeypatch.setattr(SETTINGS, "s3_allow_http", allow_http)
    monkeypatch.setattr("courtier.providers.bucket.S3Store", create_store)

    create_bucket_client()

    assert create_store.call_args.kwargs["client_options"]["allow_http"] is allow_http
