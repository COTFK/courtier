"""Fetches Lua scripts from the ProjectIgnis CardScripts repository."""

import io
import tarfile
from collections.abc import Iterable, Sequence
from typing import NamedTuple

import httpx2 as httpx

from courtier.cache_keys import ROOT_SCRIPTS, card_script_name
from courtier.logging import LOGGER
from courtier.settings import SETTINGS

ROOT_SCRIPT_FOLDERS = {"proc_unofficial.lua": ("unofficial",)}
"""Shared scripts that live in a subfolder upstream. All others are at the repository root."""


class ScriptData(NamedTuple):
    """Extracted scripts and the ETag of their source archive."""

    etag: str | None
    scripts: dict[str, str]


def _extract_scripts(
    archive: bytes, search: dict[str, Sequence[str]]
) -> dict[str, str]:
    """Reads each wanted script from the first folder in its search list that contains it."""
    found: dict[str, str] = {}
    ranks: dict[str, int] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar:
            # Paths start with the archive's top-level folder, e.g. `CardScripts-master/official/c1.lua`.
            _, _, path = member.name.partition("/")
            folder, _, name = path.rpartition("/")
            folders = search.get(name)
            if not member.isfile() or folders is None or folder not in folders:
                continue
            rank = folders.index(folder)
            if name in ranks and ranks[name] <= rank:
                continue
            if (file := tar.extractfile(member)) is None:
                continue
            data = file.read()
            ranks[name] = rank
            found[name] = data.decode("utf-8", errors="replace")
    return found


async def fetch_lua_scripts(
    card_ids: Iterable[str], etag: str | None = None
) -> ScriptData | None:
    """Fetch scripts only when the CardScripts archive differs from `etag`."""
    headers = {"If-None-Match": etag} if etag else None
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(SETTINGS.card_scripts_archive, headers=headers)
        if response.status_code == httpx.codes.NOT_MODIFIED:
            LOGGER.info("CardScripts archive is unchanged.")
            return None
        response.raise_for_status()

    search: dict[str, Sequence[str]] = {
        card_script_name(card_id): SETTINGS.card_script_folders for card_id in card_ids
    }
    search.update({name: ROOT_SCRIPT_FOLDERS.get(name, ("",)) for name in ROOT_SCRIPTS})
    found = _extract_scripts(response.content, search)
    if not found:
        raise ValueError("CardScripts archive contains none of the expected scripts.")

    LOGGER.info("Extracted %d CardScripts files.", len(found))
    return ScriptData(response.headers.get("etag"), found)
