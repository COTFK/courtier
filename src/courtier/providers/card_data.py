from typing import NamedTuple

import httpx2 as httpx

from courtier.logging import LOGGER
from courtier.models.card import Card
from courtier.settings import SETTINGS

LINK_ARROW_MARKERS = {"↙": 1, "⬇": 2, "↘": 3, "⬅": 4, "➡": 6, "↖": 7, "⬆": 8, "↗": 9}


def _subtypes(item: dict, type_parts: list[str]) -> list[str] | None:
    """Monsters list subtypes after the race; Spells and Traps use `property` instead."""
    if item["card_type"] == "Monster":
        return type_parts[1:] or None
    # A plain Normal Spell or Trap has no subtype, so it is left empty.
    prop: str | None = item.get("property")
    return None if prop in (None, "Normal") else [prop]


def _parse_card(item: dict, password: int | str) -> Card:
    """Convert a single Yaml Yugi card into Courtier format using the given password."""
    monster_type_line: str | None = item.get("monster_type_line")
    link_arrows: list[str] = item.get("link_arrows") or []
    attack = item.get("atk")
    defense = item.get("def")
    type_parts = (
        [part.strip() for part in monster_type_line.split("/")]
        if monster_type_line
        else []
    )

    return Card(
        # Preserve the previous normalization of an empty upstream password to zero.
        id=str(password) or "0",
        name=item["name"]["en"],
        archetype=item.get("series"),
        type=item["card_type"],
        race=type_parts[0] if type_parts else None,
        subtypes=_subtypes(item, type_parts),
        level=item.get("level"),
        rank=item.get("rank"),
        attribute=item.get("attribute"),
        atk=str(attack) if attack is not None else None,
        def_=str(defense) if defense is not None else None,
        scale=item.get("pendulum_scale"),
        linkval=len(link_arrows) or None,
        linkmarkers=sorted(LINK_ARROW_MARKERS[arrow] for arrow in link_arrows) or None,
        labels=item,
    )


async def _fetch_passwords_by_name(
    client: httpx.AsyncClient, names: list[str]
) -> dict[str, int | str]:
    """Look up passwords for English card names via YGOPRODeck, keyed by name."""
    response = await client.get(
        f"{SETTINGS.ygoprodeck_root}/cardinfo.php", params={"name": "|".join(names)}
    )
    # YGOPRODeck responds with 400 when none of the names match.
    if response.status_code == httpx.codes.BAD_REQUEST:
        return {}
    response.raise_for_status()

    passwords: dict[str, int | str] = {}
    for card in response.json()["data"]:
        passwords.setdefault(card["name"], card["id"])
    return passwords


async def _parse_queued_cards(
    client: httpx.AsyncClient, queued: list[dict]
) -> list[Card]:
    """Parse cards missing a Yaml Yugi password, taking passwords from YGOPRODeck."""
    if not queued:
        return []

    names = list(dict.fromkeys(item["name"]["en"] for item in queued))
    passwords = await _fetch_passwords_by_name(client, names)

    cards: list[Card] = []
    for item in queued:
        name = item["name"]["en"]
        if name not in passwords:
            LOGGER.warning("No YGOPRODeck password found for '%s'.", name)
            continue
        cards.append(_parse_card(item, passwords[name]))
    return cards


class CardData(NamedTuple):
    """Cards from Yaml Yugi and the source response's Last-Modified validator."""

    last_modified: str | None
    cards: list[Card]


def cards_url() -> str:
    return f"{SETTINGS.yaml_yugi_root}/cards.json"


async def get_card_info(last_modified: str | None) -> CardData | None:
    """Retrieve cards from Yaml Yugi with their raw records, filling in missing passwords from YGOPRODeck.

    Returns `None` without parsing if the data is unchanged since `last_modified`.
    """
    headers = {"If-Modified-Since": last_modified} if last_modified else None
    async with httpx.AsyncClient() as client:
        response = await client.get(cards_url(), headers=headers)
        if response.status_code == httpx.codes.NOT_MODIFIED:
            LOGGER.info("Card data is unchanged.")
            return None
        response.raise_for_status()

        LOGGER.info("Successfully retrieved card data. Parsing...")
        json_data: list[dict] = response.json()

        cards: list[Card] = []
        queued: list[dict] = []
        for item in json_data:
            if item.get("password") is None:
                queued.append(item)
            else:
                cards.append(_parse_card(item, item["password"]))

        LOGGER.info("Queued %d cards without a Yaml Yugi password.", len(queued))
        cards.extend(await _parse_queued_cards(client, queued))

        LOGGER.info("Parsing completed.")
        return CardData(response.headers.get("last-modified"), cards)
