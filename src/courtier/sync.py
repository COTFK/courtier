"""Keeps the database and bucket in step with YGOPRODeck and the CardScripts archive."""

import asyncio
from datetime import UTC, datetime

from obstore.store import ObjectStore

from courtier import database
from courtier.logging import LOGGER
from courtier.providers.card_data import cards_url, get_card_info
from courtier.providers.card_images import sync_card_images
from courtier.providers.card_scripts import fetch_lua_scripts
from courtier.settings import SETTINGS

_POLL_INTERVAL_SECONDS = 15 * 60
"""How often to check whether a sync is due. A failed sync is retried at the next check."""


def _last_modified_key(url: str) -> str:
    return f"{url}:last-modified"


def _etag_key(url: str) -> str:
    return f"{url}:etag"


async def _sync_cards() -> list[str]:
    url = cards_url()
    card_data = await get_card_info(
        await database.get_metadata(_last_modified_key(url))
    )
    if card_data is not None:
        await database.store_cards(
            card_data.cards, {_last_modified_key(url): card_data.last_modified}
        )
    return await database.card_ids()


async def _sync_scripts(card_ids: list[str]) -> None:
    scripts_etag_key = _etag_key(SETTINGS.card_scripts_archive)
    scripts = await fetch_lua_scripts(
        card_ids, await database.get_metadata(scripts_etag_key)
    )
    if scripts is not None:
        await database.store_scripts(scripts.scripts)
        await database.set_metadata(scripts_etag_key, scripts.etag)


async def _sync_images(bucket: ObjectStore, card_ids: list[str]) -> None:
    images = await sync_card_images(
        bucket, card_ids, await database.image_source_last_modified()
    )
    await database.store_image_source_last_modified(images.last_modified)
    if images.errors:
        raise ExceptionGroup("Card image bucket uploads failed.", images.errors)


async def sync_all(bucket: ObjectStore) -> None:
    """Refresh cards, scripts and images, saving successful work before raising any image upload failure."""
    started = datetime.now(UTC)
    LOGGER.info("Starting upstream sync.")
    card_ids = await _sync_cards()
    await _sync_scripts(card_ids)
    await _sync_images(bucket, card_ids)
    await database.set_metadata("synced_at", started.isoformat())
    LOGGER.info(
        "Upstream sync completed in %.1f seconds.",
        (datetime.now(UTC) - started).total_seconds(),
    )


async def sync_if_due(bucket: ObjectStore) -> None:
    """Run `sync_all` unless one completed within `sync_cooldown`."""
    last_synced = await database.get_metadata("synced_at")
    if (
        last_synced is not None
        and datetime.now(UTC) - datetime.fromisoformat(last_synced)
        < SETTINGS.sync_cooldown
    ):
        LOGGER.debug("Last sync is within the cooldown; skipping.")
        return
    await sync_all(bucket)


async def sync_periodically(bucket: ObjectStore) -> None:
    while True:
        try:
            await sync_if_due(bucket)
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "API sync failed; retrying in %d minutes.", _POLL_INTERVAL_SECONDS // 60
            )
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
