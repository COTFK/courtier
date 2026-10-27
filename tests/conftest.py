"""Dummy credentials and an isolated SQLite cache for each test."""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.update(
    DATABASE_PATH=":memory:",
    S3_BUCKET="test-bucket",
    S3_ENDPOINT="https://s3.example.test",
    S3_ACCESS_KEY_ID="test-key",
    S3_SECRET_ACCESS_KEY="test-secret",
)

import anyio
import httpx2 as httpx
from factories import HttpHandler
from fastapi.testclient import TestClient
from obstore.store import MemoryStore

import courtier.app as app_module
from courtier import database as cache
from courtier.models.card import Card
from courtier.settings import SETTINGS


@pytest.fixture(autouse=True)
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SETTINGS, "database_path", tmp_path / "cache.db")
    anyio.run(cache.initialize_db)


@pytest.fixture
def store() -> Callable[..., None]:
    """Seed card documents or a script snapshot outside any request."""

    def insert(*cards: Card, scripts: dict[str, str] | None = None) -> None:
        async def write() -> None:
            if cards:
                await cache.store_cards(cards, {})
            if scripts is not None:
                await cache.store_scripts(scripts)

        anyio.run(write)

    return insert


async def _no_sync(_bucket: object) -> None:
    return None


@pytest.fixture
def bucket() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def client(
    bucket: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(app_module, "create_bucket_client", lambda: bucket)
    monkeypatch.setattr(app_module, "sync_periodically", _no_sync)
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def route_http(monkeypatch: pytest.MonkeyPatch) -> Callable[[HttpHandler], None]:
    """Answers the HTTP clients that providers create internally with `handler`, so tests never touch the network."""
    real_client = httpx.AsyncClient

    def install(handler: HttpHandler) -> None:
        def client(
            *, timeout: float = 5, follow_redirects: bool = False
        ) -> httpx.AsyncClient:
            return real_client(
                transport=httpx.MockTransport(handler),
                timeout=timeout,
                follow_redirects=follow_redirects,
            )

        monkeypatch.setattr(httpx, "AsyncClient", client)

    return install
