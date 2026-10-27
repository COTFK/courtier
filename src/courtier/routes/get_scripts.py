from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import StringConstraints

from courtier import database
from courtier.cache_keys import ROOT_SCRIPTS, card_script_name
from courtier.http_cache import (
    PUBLIC_CACHE_CONTROL,
    cache_headers,
    cached_json_response,
    content_etag,
    not_modified,
)
from courtier.models.card import CardID
from courtier.routes.common import CardIDs, raise_for_missing

router = APIRouter(tags=["scripts"])

SystemScriptName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_]+\.lua$")]
SystemScriptFiles = Annotated[
    list[SystemScriptName],
    Query(
        min_length=1,
        max_length=len(ROOT_SCRIPTS),
        description="Shared CardScripts file names, repeated as `file=utility.lua&file=chain.lua`.",
        examples=[["utility.lua", "chain.lua"]],
    ),
]
_SCRIPT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/x-lua": {"schema": {"type": "string"}}}}
}


async def _serve_script(request: Request, name: str) -> Response:
    content = (await database.get_scripts([name])).get(name)
    if content is None:
        raise HTTPException(status_code=404, detail="Script not found.")
    etag = content_etag(content.encode())
    headers = cache_headers(PUBLIC_CACHE_CONTROL, etag)
    if response := not_modified(request, headers):
        return response
    return PlainTextResponse(content, media_type="text/x-lua", headers=headers)


@router.get("/scripts/cards", response_model=dict[str, str | None])
async def list_card_scripts(request: Request, ids: CardIDs) -> Response:
    """Retrieve the Lua scripts of the given cards, keyed by card ID. Cards without a script map to null. Returns 404 if any ID is unknown."""
    raise_for_missing("Cards", ids, await database.existing_card_ids(ids))
    names = {card_id: card_script_name(card_id) for card_id in ids}
    scripts = await database.get_scripts(list(names.values()))
    return cached_json_response(
        request, {card_id: scripts.get(name) for card_id, name in names.items()}
    )


@router.get(
    "/scripts/cards/{card_id}",
    response_class=Response,
    responses=_SCRIPT_RESPONSES,
)
async def get_card_script(card_id: CardID, request: Request) -> Response:
    """Retrieve the Lua script of a card by its password, such as `90681088`."""
    return await _serve_script(request, card_script_name(card_id))


@router.get("/scripts/system", response_model=dict[str, str | None])
async def list_system_scripts(file: SystemScriptFiles, request: Request) -> Response:
    """Retrieve the shared CardScripts files with the given names, keyed by file name. Known files without content map to null. Returns 404 if any name is unknown."""
    raise_for_missing("Scripts", file, ROOT_SCRIPTS)
    scripts = await database.get_scripts(file)
    return cached_json_response(request, {name: scripts.get(name) for name in file})


# Declared before `/scripts/system/{filename}` so `files` is not read as a file name.
@router.get("/scripts/system/files", response_model=list[str])
async def list_system_script_files(request: Request) -> Response:
    """Retrieve the names of the shared CardScripts files that every duel needs, such as `utility.lua`. Pass them to `/v1/scripts/system?file=` to download them."""
    return cached_json_response(request, list(ROOT_SCRIPTS))


@router.get(
    "/scripts/system/{filename}",
    response_class=Response,
    responses=_SCRIPT_RESPONSES,
)
async def get_system_script(
    filename: Annotated[
        SystemScriptName,
        Path(
            description="A shared CardScripts file name, such as `utility.lua`.",
            examples=["utility.lua"],
        ),
    ],
    request: Request,
) -> Response:
    """Retrieve a shared CardScripts file by name, such as `utility.lua`."""
    if filename not in ROOT_SCRIPTS:
        raise HTTPException(status_code=404, detail="Script not found.")
    return await _serve_script(request, filename)
