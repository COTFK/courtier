"""Downloads and refreshes card images from YGOPRODeck in an S3 bucket."""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

import httpx2 as httpx
import obstore
from obstore.store import ObjectStore

from courtier.cache_keys import card_image_key, card_thumbnail_key
from courtier.http_cache import PUBLIC_CACHE_CONTROL
from courtier.logging import LOGGER
from courtier.settings import SETTINGS


def _image_sources(card_id: str) -> tuple[tuple[str, str], ...]:
    """Pair each stored key of a card with the YGOPRODeck URL it is downloaded from."""
    name = card_image_key(card_id)
    return (
        (name, f"{SETTINGS.card_images_root}/{name}"),
        (card_thumbnail_key(card_id), f"{SETTINGS.card_thumbnails_root}/{name}"),
    )


async def _stored_image_keys(bucket: ObjectStore) -> set[str]:
    keys: set[str] = set()
    async for batch in obstore.list(bucket):
        keys.update(item["path"] for item in batch)
    return keys


_IMAGE_ATTEMPTS = 3
"""Attempts per image for rate limits, server errors and network failures, with backoff between them."""


@dataclass(frozen=True)
class _ImageSource:
    key: str
    url: str
    last_modified: str | None


@dataclass(frozen=True)
class _ImageResult:
    key: str
    last_modified: str | None


def _is_transient(response: httpx.Response) -> bool:
    return (
        response.status_code == httpx.codes.TOO_MANY_REQUESTS
        or response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
    )


async def _fetch_image(
    client: httpx.AsyncClient,
    key: str,
    source_url: str,
    last_modified: str | None,
) -> httpx.Response | None:
    """Fetch an image, returning `None` if every attempt failed."""
    headers = {"If-Modified-Since": last_modified} if last_modified else None
    for attempt in range(_IMAGE_ATTEMPTS):
        if attempt:
            await asyncio.sleep(2 ** (attempt - 1))
        try:
            response = await client.get(source_url, headers=headers)
        except httpx.HTTPError as error:
            LOGGER.warning("Could not fetch image '%s': %s", key, error)
            continue
        if not _is_transient(response):
            return response
        LOGGER.warning(
            "Could not fetch image '%s': HTTP %d.", key, response.status_code
        )
    return None


async def _download_image(
    client: httpx.AsyncClient,
    bucket: ObjectStore,
    source: _ImageSource,
    semaphore: asyncio.Semaphore,
) -> _ImageResult | None:
    """Check an image upstream and upload it when it has changed."""
    async with semaphore:
        response = await _fetch_image(
            client, source.key, source.url, source.last_modified
        )
        if response is None:
            return None

        if response.status_code == httpx.codes.NOT_MODIFIED:
            return None

        if response.status_code == httpx.codes.NOT_FOUND:
            LOGGER.debug("No image available for '%s'.", source.key)
            return None
        if not response.is_success:
            LOGGER.warning(
                "Could not fetch image '%s': HTTP %d.",
                source.key,
                response.status_code,
            )
            return None

        await obstore.put_async(
            bucket,
            source.key,
            response.content,
            attributes={
                "Content-Type": "image/jpeg",
                "Cache-Control": PUBLIC_CACHE_CONTROL,
            },
        )
    return _ImageResult(
        source.key,
        last_modified=response.headers.get("last-modified"),
    )


class ImageSyncResult(NamedTuple):
    """Source timestamps of uploaded images, and the upload failures the caller must report."""

    last_modified: dict[str, str | None]
    errors: list[Exception]


async def sync_card_images(
    bucket: ObjectStore,
    card_ids: Iterable[str],
    known_last_modified: Mapping[str, str],
) -> ImageSyncResult:
    """Store missing images and refresh existing ones when their source has changed."""
    stored = await _stored_image_keys(bucket)
    sources = {key: url for card_id in card_ids for key, url in _image_sources(card_id)}
    if not sources:
        LOGGER.info("No card images to check.")
        return ImageSyncResult({}, [])

    LOGGER.info("Checking %d card images for updates...", len(sources))
    semaphore = asyncio.Semaphore(SETTINGS.card_image_concurrency)
    async with httpx.AsyncClient(timeout=30) as client:
        results = await asyncio.gather(
            *(
                _download_image(
                    client,
                    bucket,
                    _ImageSource(
                        key=key,
                        url=url,
                        last_modified=(
                            known_last_modified.get(key) if key in stored else None
                        ),
                    ),
                    semaphore,
                )
                for key, url in sources.items()
            ),
            return_exceptions=True,
        )
    errors = [result for result in results if isinstance(result, Exception)]
    last_modified = {
        result.key: result.last_modified
        for result in results
        if isinstance(result, _ImageResult)
    }
    LOGGER.info(
        "Updated %d of %d checked card images.",
        len(last_modified),
        len(sources),
    )
    return ImageSyncResult(last_modified, errors)
