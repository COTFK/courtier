"""Sync scheduling and orchestration, with upstream providers replaced by fakes."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import anyio
import httpx2 as httpx
import pytest
from factories import HttpHandler, make_archive, make_card
from fastapi.testclient import TestClient
from obstore.exceptions import GenericError
from obstore.store import MemoryStore

import courtier.sync as sync_module
from courtier import database
from courtier.providers.card_data import CardData, cards_url
from courtier.providers.card_images import ImageSyncResult, sync_card_images
from courtier.providers.card_scripts import ScriptData
from courtier.settings import SETTINGS
from courtier.sync import (
    _POLL_INTERVAL_SECONDS,
    sync_all,
    sync_if_due,
    sync_periodically,
)

CARDS_LAST_MODIFIED = "Mon, 19 Oct 2026 07:23:52 GMT"
SCRIPTS_ETAG = '"scripts-v1"'


def test_upstream_data_is_cached_and_get_requests_do_not_fetch_it_again(
    client: TestClient,
    bucket: MemoryStore,
    route_http: Callable[[HttpHandler], None],
) -> None:
    card = {
        "password": 423705,
        "name": {"en": "Dark Magician"},
        "card_type": "Monster",
        "atk": 2500,
        "def": 2100,
    }
    archive = make_archive(
        {"official/c423705.lua": b"-- card", "utility.lua": b"-- utility"}
    )
    seen: list[httpx.Request] = []
    image = b"\xff\xd8jpeg"
    image_last_modified = "Thu, 29 Aug 2024 10:16:45 GMT"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == cards_url():
            if request.headers.get("If-Modified-Since") == CARDS_LAST_MODIFIED:
                return httpx.Response(304)
            return httpx.Response(
                200, json=[card], headers={"Last-Modified": CARDS_LAST_MODIFIED}
            )
        if str(request.url) == SETTINGS.card_scripts_archive:
            if request.headers.get("If-None-Match") == SCRIPTS_ETAG:
                return httpx.Response(httpx.codes.NOT_MODIFIED)
            return httpx.Response(200, content=archive, headers={"ETag": SCRIPTS_ETAG})
        if request.url.path.endswith("/423705.jpg"):
            if request.headers.get("If-Modified-Since") == image_last_modified:
                return httpx.Response(httpx.codes.NOT_MODIFIED)
            return httpx.Response(
                200,
                content=image,
                headers={"Last-Modified": image_last_modified},
            )
        raise AssertionError(f"unexpected upstream request: {request.url}")

    route_http(handler)
    anyio.run(sync_all, bucket)
    requests_after_sync = len(seen)

    response = client.get("/v1/cards/423705")
    assert response.json()["id"] == "00423705"
    assert response.json()["atk"] == "2500"
    assert response.json()["def"] == "2100"
    assert response.json()["labels"] == card
    assert client.get("/v1/scripts/cards/423705").text == "-- card"
    assert client.get("/v1/scripts/system/utility.lua").text == "-- utility"
    assert client.get("/v1/images/423705").content == image
    assert client.get("/v1/images/423705/thumbnail").content == image
    assert len(seen) == requests_after_sync

    anyio.run(sync_all, bucket)

    image_requests = [request for request in seen if request.url.path.endswith(".jpg")]
    assert len(image_requests) == 4
    assert (
        sum("If-Modified-Since" in request.headers for request in image_requests) == 2
    )
    archive_requests = [
        request for request in seen if str(request.url) == SETTINGS.card_scripts_archive
    ]
    assert len(archive_requests) == 2
    assert archive_requests[1].headers["If-None-Match"] == SCRIPTS_ETAG
    card_data_requests = [
        request for request in seen if str(request.url) == cards_url()
    ]
    assert len(card_data_requests) == 2
    assert card_data_requests[1].headers["If-Modified-Since"] == CARDS_LAST_MODIFIED


@pytest.mark.xfail(
    strict=True,
    reason="An unchanged archive is not re-extracted when new cards are added.",
)
def test_new_card_gets_script_from_unchanged_archive(
    client: TestClient,
    bucket: MemoryStore,
    route_http: Callable[[HttpHandler], None],
) -> None:
    cards = [{"password": 1, "name": {"en": "First"}, "card_type": "Monster"}]
    archive = make_archive(
        {"official/c1.lua": b"-- first", "official/c2.lua": b"-- second"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == cards_url():
            return httpx.Response(200, json=cards)
        if str(request.url) == SETTINGS.card_scripts_archive:
            if request.headers.get("If-None-Match") == SCRIPTS_ETAG:
                return httpx.Response(304)
            return httpx.Response(200, content=archive, headers={"ETag": SCRIPTS_ETAG})
        if request.url.path.endswith(".jpg"):
            return httpx.Response(404)
        raise AssertionError(f"unexpected upstream request: {request.url}")

    route_http(handler)
    anyio.run(sync_all, bucket)
    assert client.get("/v1/scripts/cards/1").text == "-- first"

    cards.append({"password": 2, "name": {"en": "Second"}, "card_type": "Monster"})
    anyio.run(sync_all, bucket)

    response = client.get("/v1/scripts/cards/2")
    assert response.status_code == 200
    assert response.text == "-- second"


@dataclass
class Upstream:
    card_data: CardData | None
    scripts: dict[str, str]
    archive_error: Exception | None = None
    script_etag: str = SCRIPTS_ETAG
    card_last_modified_values: list[str | None] = field(default_factory=list)
    script_requests: list[list[str]] = field(default_factory=list)
    script_etags: list[str | None] = field(default_factory=list)
    image_requests: list[list[str]] = field(default_factory=list)

    async def get_card_info(self, last_modified: str | None) -> CardData | None:
        self.card_last_modified_values.append(last_modified)
        if self.card_data is None or last_modified == self.card_data.last_modified:
            return None
        return self.card_data

    async def fetch_lua_scripts(
        self, card_ids: list[str], etag: str | None
    ) -> ScriptData | None:
        self.script_requests.append(list(card_ids))
        self.script_etags.append(etag)
        if self.archive_error is not None:
            raise self.archive_error
        if etag == self.script_etag:
            return None
        return ScriptData(self.script_etag, self.scripts)

    async def sync_card_images(
        self, _bucket: MemoryStore, card_ids: list[str], _known: Mapping[str, str]
    ) -> ImageSyncResult:
        self.image_requests.append(list(card_ids))
        return ImageSyncResult({}, [])


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Upstream:
    fake = Upstream(
        card_data=CardData(CARDS_LAST_MODIFIED, [make_card()]),
        scripts={"c89631139.lua": "-- script"},
    )
    monkeypatch.setattr(sync_module, "get_card_info", fake.get_card_info)
    monkeypatch.setattr(sync_module, "fetch_lua_scripts", fake.fetch_lua_scripts)
    monkeypatch.setattr(sync_module, "sync_card_images", fake.sync_card_images)
    return fake


def test_skips_upstream_when_synced_within_cooldown(
    upstream: Upstream, bucket: MemoryStore
) -> None:
    anyio.run(database.set_metadata, "synced_at", datetime.now(UTC).isoformat())

    anyio.run(sync_if_due, bucket)

    assert upstream.card_last_modified_values == []


def test_syncs_after_cooldown_has_passed(
    upstream: Upstream, bucket: MemoryStore
) -> None:
    anyio.run(
        database.set_metadata,
        "synced_at",
        (datetime.now(UTC) - timedelta(days=2)).isoformat(),
    )

    anyio.run(sync_if_due, bucket)

    assert upstream.script_requests == [["89631139"]]


def test_first_sync_stores_cards_scripts_images_and_timestamp(
    upstream: Upstream, bucket: MemoryStore
) -> None:
    anyio.run(sync_if_due, bucket)

    assert anyio.run(database.card_ids) == ["89631139"]
    assert anyio.run(database.get_scripts, ["c89631139.lua"]) == upstream.scripts
    assert upstream.image_requests == [["89631139"]]
    assert anyio.run(database.get_metadata, "synced_at") is not None
    assert (
        anyio.run(database.get_metadata, f"{cards_url()}:last-modified")
        == CARDS_LAST_MODIFIED
    )


def test_cards_and_scripts_are_served_before_images_finish(
    client: TestClient,
    upstream: Upstream,
    bucket: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_ids: list[str] = []

    async def downloading(
        _bucket: MemoryStore, card_ids: list[str], _known: Mapping[str, str]
    ) -> ImageSyncResult:
        image_ids.extend(card_ids)
        assert await database.get_metadata("synced_at") is None
        assert client.get("/v1/cards/89631139").status_code == 200
        assert client.get("/v1/scripts/cards/89631139").text == "-- script"
        return ImageSyncResult({}, [])

    monkeypatch.setattr(sync_module, "sync_card_images", downloading)

    anyio.run(sync_all, bucket)

    assert upstream.script_requests == [["89631139"]]
    assert image_ids == ["89631139"]


def test_image_upload_failure_keeps_other_timestamps_and_retries_the_sync(
    upstream: Upstream,
    bucket: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploaded = "Thu, 29 Aug 2024 10:16:45 GMT"

    async def partly_failing(
        _bucket: MemoryStore, _card_ids: list[str], _known: Mapping[str, str]
    ) -> ImageSyncResult:
        return ImageSyncResult(
            {"thumbnails/89631139.jpg": uploaded},
            [GenericError("bucket unavailable")],
        )

    monkeypatch.setattr(sync_module, "sync_card_images", partly_failing)

    with pytest.raises(ExceptionGroup) as error:
        anyio.run(sync_all, bucket)

    assert [type(exc) for exc in error.value.exceptions] == [GenericError]
    assert anyio.run(database.image_source_last_modified) == {
        "thumbnails/89631139.jpg": uploaded
    }
    assert anyio.run(database.get_metadata, "synced_at") is None
    assert upstream.script_requests == [["89631139"]]


@pytest.mark.parametrize("image_available", [False, True])
@pytest.mark.usefixtures("upstream")
def test_image_download_failures_do_not_invalidate_the_cache(
    *,
    image_available: bool,
    client: TestClient,
    bucket: MemoryStore,
    route_http: Callable[[HttpHandler], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (
            image_available
            and str(request.url) == f"{SETTINGS.card_images_root}/89631139.jpg"
        ):
            return httpx.Response(200, content=b"\xff\xd8jpeg")
        return httpx.Response(503)

    route_http(handler)
    monkeypatch.setattr(sync_module, "sync_card_images", sync_card_images)

    anyio.run(sync_all, bucket)

    assert anyio.run(database.get_metadata, "synced_at") is not None
    assert client.get("/v1/cards/89631139").status_code == 200
    assert client.get("/v1/scripts/cards/89631139").text == "-- script"
    assert client.get("/v1/images/89631139").status_code == (
        200 if image_available else 404
    )
    assert client.get("/v1/images/89631139/thumbnail").status_code == 404


def test_failed_refresh_keeps_serving_cached_data(
    client: TestClient,
    upstream: Upstream,
    bucket: MemoryStore,
    store: Callable[..., None],
) -> None:
    store(make_card(), scripts={"utility.lua": "-- cached"})
    previous_sync = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    anyio.run(database.set_metadata, "synced_at", previous_sync)
    upstream.archive_error = ValueError("archive unavailable")

    with pytest.raises(ValueError, match="archive unavailable"):
        anyio.run(sync_if_due, bucket)

    assert anyio.run(database.get_metadata, "synced_at") == previous_sync
    assert client.get("/v1/cards/89631139").status_code == 200
    assert client.get("/v1/scripts/system/utility.lua").text == "-- cached"


def test_failed_archive_keeps_cards_and_retries_on_next_sync(
    client: TestClient, upstream: Upstream, bucket: MemoryStore
) -> None:
    upstream.archive_error = ValueError("invalid archive")
    with pytest.raises(ValueError, match="invalid archive"):
        anyio.run(sync_if_due, bucket)

    assert anyio.run(database.card_ids) == ["89631139"]
    assert anyio.run(database.get_metadata, "synced_at") is None
    assert client.get("/v1/cards/89631139").status_code == 200
    assert client.get("/v1/scripts/cards/89631139").status_code == 404

    upstream.archive_error = None
    anyio.run(sync_if_due, bucket)

    assert upstream.card_last_modified_values == [None, CARDS_LAST_MODIFIED]
    assert upstream.script_requests[-1] == ["89631139"]
    assert anyio.run(database.get_scripts, ["c89631139.lua"]) == upstream.scripts
    assert anyio.run(database.get_metadata, "synced_at") is not None


@pytest.mark.usefixtures("upstream")
def test_scripts_are_replaced_by_the_archive(
    bucket: MemoryStore,
) -> None:
    anyio.run(
        database.store_scripts, {"c89631139.lua": "-- old", "c1.lua": "-- removed"}
    )

    anyio.run(sync_if_due, bucket)

    assert anyio.run(database.get_scripts, ["c89631139.lua", "c1.lua"]) == {
        "c89631139.lua": "-- script"
    }


def test_periodic_sync_survives_a_failed_sync(
    monkeypatch: pytest.MonkeyPatch, bucket: MemoryStore
) -> None:
    calls: list[MemoryStore] = []
    sleeps: list[float] = []

    class StopLoopError(Exception):
        pass

    async def failing_then_ok(store: MemoryStore) -> None:
        calls.append(store)
        if len(calls) == 1:
            raise RuntimeError("upstream unavailable")

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise StopLoopError

    monkeypatch.setattr(sync_module, "sync_if_due", failing_then_ok)
    monkeypatch.setattr(sync_module, "asyncio", SimpleNamespace(sleep=sleep))

    with pytest.raises(StopLoopError):
        anyio.run(sync_periodically, bucket)

    assert len(calls) == 2
    assert sleeps == [_POLL_INTERVAL_SECONDS, _POLL_INTERVAL_SECONDS]
