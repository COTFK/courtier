from fastapi import APIRouter, HTTPException, Request, Response

from courtier import database
from courtier.http_cache import cached_json_response
from courtier.models.card import Card, CardID
from courtier.routes.common import CardIDs, raise_for_missing

router = APIRouter(tags=["cards"])


@router.get("/cards", response_model=list[Card])
async def list_cards(ids: CardIDs, request: Request) -> Response:
    """Retrieve the cards with the given IDs, each with its Yaml Yugi labels. Returns 404 if any ID is unknown."""
    cards = await database.get_cards(ids)
    raise_for_missing("Cards", ids, (card.id for card in cards))
    return cached_json_response(request, cards)


@router.get("/cards/{card_id}", response_model=Card)
async def get_card(card_id: CardID, request: Request) -> Response:
    """Retrieve a card by its ID, with its Yaml Yugi labels."""
    if (card := await database.get_card(card_id)) is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    return cached_json_response(request, card)
