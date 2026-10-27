from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from obstore import get_async
from obstore.exceptions import BaseError
from obstore.store import ObjectStore

from courtier import database
from courtier.cache_keys import card_image_key, card_thumbnail_key
from courtier.http_cache import PUBLIC_CACHE_CONTROL, cache_headers, not_modified
from courtier.logging import LOGGER
from courtier.models.card import CardID
from courtier.routes.common import Bucket

router = APIRouter(tags=["cards"])

_IMAGE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}}}
}


async def _serve_image(
    request: Request, bucket: ObjectStore, key: str, card_id: str
) -> Response:
    # Only cards we know about are served, so arbitrary requests never reach the bucket.
    if not await database.has_card(card_id):
        raise HTTPException(status_code=404, detail="Image not found.")
    try:
        image = await get_async(bucket, key)
        headers = cache_headers(PUBLIC_CACHE_CONTROL, image.meta["e_tag"])
        if response := not_modified(request, headers):
            return response
        body = bytes(await image.bytes_async())
    except FileNotFoundError as error:
        LOGGER.debug("Image '%s' is not in the bucket.", key)
        raise HTTPException(status_code=404, detail="Image not found.") from error
    except BaseError as error:
        LOGGER.exception("Bucket request failed for card '%s'.", card_id)
        raise HTTPException(
            status_code=503, detail="Image storage unavailable."
        ) from error
    return Response(body, media_type="image/jpeg", headers=headers)


@router.get(
    "/images/{card_id}",
    response_class=Response,
    responses=_IMAGE_RESPONSES,
)
async def get_card_image(
    card_id: CardID,
    request: Request,
    bucket: Bucket,
) -> Response:
    """Retrieve the full-size JPEG image of a card, such as `/v1/images/89631139`."""
    return await _serve_image(request, bucket, card_image_key(card_id), card_id)


@router.get(
    "/images/{card_id}/thumbnail",
    response_class=Response,
    responses=_IMAGE_RESPONSES,
)
async def get_card_thumbnail(
    card_id: CardID,
    request: Request,
    bucket: Bucket,
) -> Response:
    """Retrieve the small JPEG image of a card, such as `/v1/images/89631139/thumbnail`."""
    return await _serve_image(request, bucket, card_thumbnail_key(card_id), card_id)
