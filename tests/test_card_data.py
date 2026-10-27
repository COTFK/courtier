from collections.abc import Callable, Sequence

import anyio
import httpx2 as httpx
import pytest
from factories import HttpHandler
from pydantic import ValidationError

from courtier.providers.card_data import _parse_card, get_card_info

LAST_MODIFIED = "Mon, 19 Oct 2026 07:23:52 GMT"
CARDS = (
    {
        "password": "89631139",
        "name": {"en": "Blue-Eyes White Dragon"},
        "card_type": "Monster",
        "monster_type_line": "Dragon / Normal",
        "level": 8,
        "attribute": "LIGHT",
        "atk": "3000",
        "def": "2500",
    },
    {"name": {"en": "Queued A"}, "card_type": "Spell", "property": "Normal"},
    {"name": {"en": "Queued B"}, "card_type": "Spell", "property": "Normal"},
    {"name": {"en": "Unknown"}, "card_type": "Spell", "property": "Normal"},
)


def yaml_yugi(
    *,
    cards: Sequence[dict] = CARDS,
    lookup: HttpHandler | None = None,
    seen: list[httpx.Request] | None = None,
    conditional_status: int = httpx.codes.NOT_MODIFIED,
    cards_status: int = httpx.codes.OK,
) -> HttpHandler:
    """Serves Yaml Yugi and YGOPRODeck lookups, recording every request in `seen`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/cards.json"):
            if request.headers.get("If-Modified-Since") == LAST_MODIFIED:
                return httpx.Response(conditional_status)
            return httpx.Response(
                cards_status,
                json=list(cards),
                headers={"Last-Modified": LAST_MODIFIED},
            )
        assert lookup is not None, f"unexpected request to {request.url}"
        return lookup(request)

    return handler


def test_monster_race_and_subtypes_come_from_type_line() -> None:
    item = {
        "name": {"en": "Blue-Eyes White Dragon"},
        "card_type": "Monster",
        "monster_type_line": "Dragon / Normal",
        "level": 8,
        "attribute": "LIGHT",
        "atk": "3000",
        "def": "2500",
    }

    card = _parse_card(item, "89631139")

    assert card.id == "89631139"
    assert card.race == "Dragon"
    assert card.subtypes == ["Normal"]
    assert card.def_ == "2500"


def test_link_arrows_become_link_value_and_sorted_markers() -> None:
    item = {
        "name": {"en": "Link Monster"},
        "card_type": "Monster",
        "monster_type_line": "Cyberse / Link / Effect",
        "link_arrows": ["⬆", "➡"],
    }

    card = _parse_card(item, "00123456")

    assert card.linkval == 2
    assert card.linkmarkers == [6, 8]


@pytest.mark.parametrize(
    ("attack", "defense", "expected_attack", "expected_defense"),
    [
        (3000, 2500, "3000", "2500"),
        (0, 0, "0", "0"),
        ("?", "?", "?", "?"),
        (None, None, None, None),
    ],
)
def test_combat_stats_remain_strings(
    attack: int | str | None,
    defense: int | str | None,
    expected_attack: str | None,
    expected_defense: str | None,
) -> None:
    item = {
        "name": {"en": "Monster"},
        "card_type": "Monster",
        "atk": attack,
        "def": defense,
    }

    card = _parse_card(item, "00000001")

    assert card.atk == expected_attack
    assert card.def_ == expected_defense
    assert card.labels == item


def test_spell_subtype_comes_from_property() -> None:
    item = {"name": {"en": "Pot"}, "card_type": "Spell", "property": "Quick-Play"}

    assert _parse_card(item, "00000001").subtypes == ["Quick-Play"]


def test_plain_normal_spell_has_no_subtype() -> None:
    item = {"name": {"en": "Dark Hole"}, "card_type": "Spell", "property": "Normal"}

    assert _parse_card(item, "00000002").subtypes is None


def test_card_keeps_its_yaml_yugi_record_as_labels() -> None:
    item = {"name": {"en": "Pot"}, "card_type": "Spell", "property": "Quick-Play"}

    assert _parse_card(item, "00000001").labels == item


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        (423705, "00423705"),
        ("423705", "00423705"),
        ("00423705", "00423705"),
        ("", "00000000"),
    ],
)
def test_upstream_passwords_are_normalized(password: int | str, expected: str) -> None:
    item = {"name": {"en": "Pot"}, "card_type": "Spell"}

    assert _parse_card(item, password).id == expected


def test_invalid_upstream_password_is_rejected() -> None:
    item = {"name": {"en": "Pot"}, "card_type": "Spell"}

    with pytest.raises(ValidationError):
        _parse_card(item, "invalid")


def test_unchanged_data_returns_none_from_conditional_get(
    route_http: Callable[[HttpHandler], None],
) -> None:
    seen: list[httpx.Request] = []
    route_http(yaml_yugi(seen=seen))

    assert anyio.run(get_card_info, LAST_MODIFIED) is None

    [request] = seen
    assert request.method == "GET"
    assert request.headers["If-Modified-Since"] == LAST_MODIFIED


def test_cards_with_passwords_skip_ygoprodeck(
    route_http: Callable[[HttpHandler], None],
) -> None:
    seen: list[httpx.Request] = []
    route_http(yaml_yugi(cards=CARDS[:1], seen=seen))

    data = anyio.run(get_card_info, None)

    assert data is not None
    assert data.last_modified == LAST_MODIFIED
    assert [card.id for card in data.cards] == ["89631139"]
    [request] = seen
    assert "If-Modified-Since" not in request.headers
    assert request.method == "GET"
    assert request.headers["Accept-Encoding"] != "identity"


def test_queued_cards_take_passwords_from_ygoprodeck(
    route_http: Callable[[HttpHandler], None],
) -> None:
    names: list[str] = []

    def lookup(request: httpx.Request) -> httpx.Response:
        names.append(request.url.params["name"])
        return httpx.Response(
            httpx.codes.OK,
            json={
                "data": [
                    {"id": 423705, "name": "Queued A"},
                    {"id": 1, "name": "Queued A"},
                    {"id": 1234, "name": "Queued B"},
                ]
            },
        )

    route_http(yaml_yugi(lookup=lookup))

    data = anyio.run(get_card_info, None)

    assert data is not None
    assert names == ["Queued A|Queued B|Unknown"]
    assert {card.name: card.id for card in data.cards} == {
        "Blue-Eyes White Dragon": "89631139",
        "Queued A": "00423705",
        "Queued B": "00001234",
    }


def test_queued_cards_without_ygoprodeck_match_are_skipped(
    route_http: Callable[[HttpHandler], None],
) -> None:
    route_http(
        yaml_yugi(lookup=lambda _request: httpx.Response(httpx.codes.BAD_REQUEST))
    )

    data = anyio.run(get_card_info, None)

    assert data is not None
    assert [card.id for card in data.cards] == ["89631139"]


def test_ygoprodeck_server_error_fails_the_sync(
    route_http: Callable[[HttpHandler], None],
) -> None:
    route_http(
        yaml_yugi(
            lookup=lambda _request: httpx.Response(httpx.codes.INTERNAL_SERVER_ERROR)
        )
    )

    with pytest.raises(httpx.HTTPStatusError):
        anyio.run(get_card_info, None)


def test_conditional_get_server_error_fails_the_sync(
    route_http: Callable[[HttpHandler], None],
) -> None:
    route_http(
        yaml_yugi(
            conditional_status=httpx.codes.INTERNAL_SERVER_ERROR,
        )
    )

    with pytest.raises(httpx.HTTPStatusError):
        anyio.run(get_card_info, LAST_MODIFIED)


def test_download_server_error_fails_the_sync(
    route_http: Callable[[HttpHandler], None],
) -> None:
    route_http(yaml_yugi(cards_status=httpx.codes.SERVICE_UNAVAILABLE))

    with pytest.raises(httpx.HTTPStatusError):
        anyio.run(get_card_info, None)
