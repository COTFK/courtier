import sqlite3
from collections.abc import Iterator
from contextlib import closing

import anyio
import pytest
from factories import make_card

from courtier import database
from courtier.models.card import Card
from courtier.settings import SETTINGS

LAST_MODIFIED_KEY = "cards.json:last-modified"
LAST_MODIFIED = "Mon, 19 Oct 2026 07:23:52 GMT"


def test_card_refresh_handles_many_rows_and_duplicate_ids() -> None:
    cards = [make_card(f"{number:08d}") for number in range(2000)]
    cards.append(make_card("00000001", name="Last duplicate wins"))

    anyio.run(database.store_cards, cards, {LAST_MODIFIED_KEY: LAST_MODIFIED})
    anyio.run(
        database.store_cards,
        [make_card("00000000", name="Updated")],
        {LAST_MODIFIED_KEY: "Tue, 20 Oct 2026 07:23:52 GMT"},
    )

    ids = anyio.run(database.card_ids)
    stored = {card.id: card.name for card in anyio.run(database.get_cards, ids)}
    assert len(stored) == 2000
    assert stored["00000000"] == "Updated"
    assert stored["00000001"] == "Last duplicate wins"
    assert (
        anyio.run(database.get_metadata, LAST_MODIFIED_KEY)
        == "Tue, 20 Oct 2026 07:23:52 GMT"
    )


def test_card_write_failure_preserves_cards_and_etag() -> None:
    cached = make_card()
    anyio.run(database.store_cards, [cached], {LAST_MODIFIED_KEY: LAST_MODIFIED})

    def failing_cards() -> Iterator[Card]:
        yield make_card(name="Uncommitted change")
        raise RuntimeError("card stream failed")

    with pytest.raises(RuntimeError, match="card stream failed"):
        anyio.run(
            database.store_cards,
            failing_cards(),
            {LAST_MODIFIED_KEY: "Tue, 20 Oct 2026 07:23:52 GMT"},
        )

    assert anyio.run(database.get_card, cached.id) == cached
    assert anyio.run(database.get_metadata, LAST_MODIFIED_KEY) == LAST_MODIFIED


def test_script_replacement_rolls_back_on_failure() -> None:
    anyio.run(database.store_scripts, {"utility.lua": "-- cached"})
    with closing(sqlite3.connect(SETTINGS.database_path)) as db:
        db.execute("""
            CREATE TRIGGER reject_script BEFORE INSERT ON scripts
            WHEN NEW.name = 'bad.lua'
            BEGIN SELECT RAISE(ABORT, 'write failed'); END
        """)
        db.commit()

    with pytest.raises(sqlite3.IntegrityError, match="write failed"):
        anyio.run(database.store_scripts, {"new.lua": "-- new", "bad.lua": "-- bad"})

    assert anyio.run(database.get_scripts, ["utility.lua", "new.lua"]) == {
        "utility.lua": "-- cached"
    }


def test_initialization_preserves_cache_across_connections() -> None:
    card = make_card("423705")
    anyio.run(database.store_cards, [card], {LAST_MODIFIED_KEY: LAST_MODIFIED})
    anyio.run(database.store_scripts, {"utility.lua": "-- utility"})
    anyio.run(database.set_metadata, "synced_at", "2026-10-24T00:00:00+00:00")

    anyio.run(database.initialize_db)

    assert anyio.run(database.get_card, "00423705") == card
    assert anyio.run(database.has_card, "00423705")
    assert not anyio.run(database.has_card, "00000001")
    assert anyio.run(database.get_scripts, ["utility.lua", "missing.lua"]) == {
        "utility.lua": "-- utility"
    }
    assert anyio.run(database.get_metadata, LAST_MODIFIED_KEY) == LAST_MODIFIED
    assert anyio.run(database.get_metadata, "synced_at") == "2026-10-24T00:00:00+00:00"


def test_lookups_bind_values_and_accept_empty_batches() -> None:
    card = make_card()
    anyio.run(database.store_cards, [card], {})
    anyio.run(database.store_scripts, {"utility.lua": "-- utility"})

    assert anyio.run(database.get_cards, [card.id, "'); DROP TABLE cards; --"]) == [
        card
    ]
    assert anyio.run(
        database.get_scripts, ["utility.lua", "'); DROP TABLE scripts; --"]
    ) == {"utility.lua": "-- utility"}
    assert anyio.run(database.get_cards, []) == []
    assert anyio.run(database.get_scripts, []) == {}
