import asyncio
from collections.abc import Callable

import anyio
import httpx2 as httpx
import obstore
import pytest
from factories import HttpHandler
from obstore.exceptions import GenericError
from obstore.store import MemoryStore

import courtier.providers.card_images as image_provider
from courtier.providers.card_images import ImageSyncResult, sync_card_images
from courtier.settings import SETTINGS

IMAGE_URL = f"{SETTINGS.card_images_root}/423705.jpg"
THUMBNAIL_URL = f"{SETTINGS.card_thumbnails_root}/423705.jpg"
JPEG = b"\xff\xd8jpeg"
LAST_MODIFIED = "Thu, 29 Aug 2024 10:16:45 GMT"


def run_sync(
    bucket: MemoryStore, known: dict[str, str] | None = None
) -> ImageSyncResult:
    return anyio.run(sync_card_images, bucket, ["00423705"], known or {})


def stored_keys(bucket: MemoryStore) -> list[str]:
    return sorted(item["path"] for batch in obstore.list(bucket) for item in batch)


def test_image_downloads_respect_concurrency_limit(
    bucket: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SETTINGS, "card_image_concurrency", 2)
    active = 0
    peak = 0
    entered = 0

    async def check() -> ImageSyncResult:
        started = asyncio.Event()

        async def fetch(
            _client: httpx.AsyncClient,
            _key: str,
            _url: str,
            _last_modified: str | None,
        ) -> httpx.Response:
            nonlocal active, peak, entered
            active += 1
            entered += 1
            peak = max(peak, active)
            if active == 2:
                started.set()
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                await asyncio.sleep(0)
                return httpx.Response(httpx.codes.NOT_FOUND)
            finally:
                active -= 1

        monkeypatch.setattr(image_provider, "_fetch_image", fetch)
        return await sync_card_images(bucket, ["1", "2", "3"], {})

    result = anyio.run(check)

    assert result == ImageSyncResult({}, [])
    assert entered == 6
    assert peak == 2


def scripted(
    respond: Callable[[str, int], httpx.Response],
) -> tuple[HttpHandler, dict[str, int]]:
    """Answers each request with `respond(url, attempt)` and counts attempts per URL."""
    attempts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        attempts[url] = attempts.get(url, 0) + 1
        return respond(url, attempts[url])

    return handler, attempts


def refuse(_url: str, _attempt: int) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Records retry backoff delays without waiting for them."""
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def record(delay: float) -> None:
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", record)
    return delays


def test_downloads_image_and_thumbnail(
    route_http: Callable[[HttpHandler], None], bucket: MemoryStore
) -> None:
    route_http(
        lambda _request: httpx.Response(
            200, content=JPEG, headers={"Last-Modified": LAST_MODIFIED}
        )
    )

    result = run_sync(bucket)

    assert stored_keys(bucket) == ["423705.jpg", "thumbnails/423705.jpg"]
    image = obstore.get(bucket, "423705.jpg")
    assert bytes(image.bytes()) == JPEG
    assert image.attributes == {
        "Content-Type": "image/jpeg",
        "Cache-Control": "public, max-age=900, s-maxage=900",
    }
    assert result.last_modified == {
        "423705.jpg": LAST_MODIFIED,
        "thumbnails/423705.jpg": LAST_MODIFIED,
    }


def test_images_already_stored_are_checked_conditionally(
    route_http: Callable[[HttpHandler], None], bucket: MemoryStore
) -> None:
    obstore.put(bucket, "423705.jpg", JPEG)
    obstore.put(bucket, "thumbnails/423705.jpg", JPEG)
    known = {"423705.jpg": LAST_MODIFIED, "thumbnails/423705.jpg": LAST_MODIFIED}
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[str(request.url)] = request.headers.get("if-modified-since")
        return httpx.Response(httpx.codes.NOT_MODIFIED)

    route_http(handler)

    result = run_sync(bucket, known)

    assert seen == {IMAGE_URL: LAST_MODIFIED, THUMBNAIL_URL: LAST_MODIFIED}
    assert stored_keys(bucket) == ["423705.jpg", "thumbnails/423705.jpg"]
    assert result == ImageSyncResult({}, [])


def test_missing_images_download_while_existing_images_are_checked(
    route_http: Callable[[HttpHandler], None], bucket: MemoryStore
) -> None:
    obstore.put(bucket, "423705.jpg", JPEG)
    known = {"423705.jpg": LAST_MODIFIED}
    handler, attempts = scripted(
        lambda url, _attempt: httpx.Response(
            httpx.codes.NOT_MODIFIED if url == IMAGE_URL else httpx.codes.OK,
            content=b"" if url == IMAGE_URL else JPEG,
            headers={} if url == IMAGE_URL else {"Last-Modified": LAST_MODIFIED},
        )
    )
    route_http(handler)

    result = run_sync(bucket, known)

    assert attempts == {IMAGE_URL: 1, THUMBNAIL_URL: 1}
    assert stored_keys(bucket) == ["423705.jpg", "thumbnails/423705.jpg"]
    assert result.last_modified == {"thumbnails/423705.jpg": LAST_MODIFIED}


def test_changed_image_replaces_old_bucket_object(
    route_http: Callable[[HttpHandler], None], bucket: MemoryStore
) -> None:
    old_image = b"\xff\xd8old"
    new_image = b"\xff\xd8new"
    new_last_modified = "Fri, 13 Sep 2024 10:16:45 GMT"
    obstore.put(bucket, "423705.jpg", old_image)
    obstore.put(bucket, "thumbnails/423705.jpg", old_image)
    known = {"423705.jpg": LAST_MODIFIED, "thumbnails/423705.jpg": LAST_MODIFIED}
    route_http(
        lambda request: (
            httpx.Response(
                httpx.codes.OK,
                content=new_image,
                headers={"Last-Modified": new_last_modified},
            )
            if str(request.url) == IMAGE_URL
            else httpx.Response(httpx.codes.NOT_MODIFIED)
        )
    )

    result = run_sync(bucket, known)

    assert bytes(obstore.get(bucket, "423705.jpg").bytes()) == new_image
    assert bytes(obstore.get(bucket, "thumbnails/423705.jpg").bytes()) == old_image
    assert result.last_modified == {"423705.jpg": new_last_modified}


@pytest.mark.parametrize("status", [403, 404])
def test_client_errors_are_not_retried(
    route_http: Callable[[HttpHandler], None],
    bucket: MemoryStore,
    sleeps: list[float],
    status: int,
) -> None:
    handler, attempts = scripted(lambda _url, _attempt: httpx.Response(status))
    route_http(handler)

    run_sync(bucket)

    assert attempts == {IMAGE_URL: 1, THUMBNAIL_URL: 1}
    assert sleeps == []
    assert stored_keys(bucket) == []


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_failure_is_retried_once(
    route_http: Callable[[HttpHandler], None],
    bucket: MemoryStore,
    sleeps: list[float],
    status: int,
) -> None:
    handler, attempts = scripted(
        lambda _url, attempt: httpx.Response(
            status if attempt == 1 else httpx.codes.OK, content=JPEG
        )
    )
    route_http(handler)

    result = run_sync(bucket)

    assert attempts == {IMAGE_URL: 2, THUMBNAIL_URL: 2}
    assert sleeps == [1, 1]
    assert stored_keys(bucket) == ["423705.jpg", "thumbnails/423705.jpg"]
    assert result.last_modified == {"423705.jpg": None, "thumbnails/423705.jpg": None}


def test_persistent_server_errors_give_up_after_three_attempts(
    route_http: Callable[[HttpHandler], None],
    bucket: MemoryStore,
    sleeps: list[float],
) -> None:
    handler, attempts = scripted(
        lambda _url, _attempt: httpx.Response(httpx.codes.SERVICE_UNAVAILABLE)
    )
    route_http(handler)

    run_sync(bucket)

    assert attempts == {IMAGE_URL: 3, THUMBNAIL_URL: 3}
    assert sorted(sleeps) == [1, 1, 2, 2]
    assert stored_keys(bucket) == []


def test_network_failures_are_retried_like_server_errors(
    route_http: Callable[[HttpHandler], None],
    bucket: MemoryStore,
    sleeps: list[float],
) -> None:
    handler, attempts = scripted(refuse)
    route_http(handler)

    run_sync(bucket)

    assert attempts == {IMAGE_URL: 3, THUMBNAIL_URL: 3}
    assert sorted(sleeps) == [1, 1, 2, 2]
    assert stored_keys(bucket) == []


def test_bucket_upload_failure_is_raised_after_other_uploads(
    route_http: Callable[[HttpHandler], None],
    bucket: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_http(
        lambda _request: httpx.Response(
            200, content=JPEG, headers={"Last-Modified": LAST_MODIFIED}
        )
    )
    upload = obstore.put_async

    async def failing_upload(
        store: MemoryStore,
        key: str,
        data: bytes,
        *,
        attributes: dict[str, str] | None = None,
    ) -> None:
        if key == "423705.jpg":
            raise GenericError("bucket unavailable")
        await upload(store, key, data, attributes=attributes)

    monkeypatch.setattr(obstore, "put_async", failing_upload)

    result = run_sync(bucket)

    assert [type(exc) for exc in result.errors] == [GenericError]
    assert result.last_modified == {"thumbnails/423705.jpg": LAST_MODIFIED}
    assert stored_keys(bucket) == ["thumbnails/423705.jpg"]
