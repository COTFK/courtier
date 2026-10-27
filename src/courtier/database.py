"""SQLite cache: card documents, script text, and sync metadata."""

from collections.abc import Iterable, Mapping, Sequence

import aiosqlite

from courtier.models.card import Card
from courtier.settings import SETTINGS


def _connect() -> aiosqlite.Connection:
    return aiosqlite.connect(SETTINGS.database_path)


async def initialize_db() -> None:
    async with _connect() as db:
        await db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scripts (
                name TEXT PRIMARY KEY,
                content TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS image_sources (
                path TEXT PRIMARY KEY,
                last_modified TEXT
            );
        """)


async def get_cards(ids: Sequence[str]) -> list[Card]:
    placeholders = ", ".join("?" for _ in ids)
    async with _connect() as db:
        rows = await db.execute_fetchall(
            f"SELECT data FROM cards WHERE id IN ({placeholders})",  # noqa: S608
            ids,
        )
    return [Card.model_validate_json(row[0]) for row in rows]


async def get_card(card_id: str) -> Card | None:
    cards = await get_cards([card_id])
    return cards[0] if cards else None


async def has_card(card_id: str) -> bool:
    async with (
        _connect() as db,
        db.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)) as cursor,
    ):
        return await cursor.fetchone() is not None


async def existing_card_ids(ids: Sequence[str]) -> set[str]:
    placeholders = ", ".join("?" for _ in ids)
    async with _connect() as db:
        rows = await db.execute_fetchall(
            f"SELECT id FROM cards WHERE id IN ({placeholders})",  # noqa: S608
            ids,
        )
    return {row[0] for row in rows}


async def card_ids() -> list[str]:
    async with _connect() as db:
        rows = await db.execute_fetchall("SELECT id FROM cards ORDER BY id")
    return [row[0] for row in rows]


async def get_scripts(names: Sequence[str]) -> dict[str, str]:
    placeholders = ", ".join("?" for _ in names)
    async with _connect() as db:
        rows = await db.execute_fetchall(
            f"SELECT name, content FROM scripts WHERE name IN ({placeholders})",  # noqa: S608
            names,
        )
    return {row[0]: row[1] for row in rows}


async def get_metadata(key: str) -> str | None:
    async with (
        _connect() as db,
        db.execute("SELECT value FROM metadata WHERE key = ?", (key,)) as cursor,
    ):
        row = await cursor.fetchone()
    return row[0] if row else None


async def set_metadata(key: str, value: str | None) -> None:
    async with _connect() as db:
        await db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value))
        await db.commit()


async def image_source_last_modified() -> dict[str, str]:
    async with _connect() as db:
        rows = await db.execute_fetchall(
            "SELECT path, last_modified FROM image_sources "
            "WHERE last_modified IS NOT NULL"
        )
    return {row[0]: row[1] for row in rows}


async def store_image_source_last_modified(
    values: Mapping[str, str | None],
) -> None:
    if not values:
        return
    async with _connect() as db:
        await db.executemany(
            """
            INSERT INTO image_sources VALUES (?, ?)
            ON CONFLICT(path) DO UPDATE SET last_modified = excluded.last_modified
            """,
            values.items(),
        )
        await db.commit()


async def store_cards(
    cards: Iterable[Card], metadata: Mapping[str, str | None]
) -> None:
    async with _connect() as db:
        await db.executemany(
            "INSERT OR REPLACE INTO cards VALUES (?, ?)",
            ((card.id, card.model_dump_json()) for card in cards),
        )
        await db.executemany(
            "INSERT OR REPLACE INTO metadata VALUES (?, ?)", metadata.items()
        )
        await db.commit()


async def store_scripts(scripts: dict[str, str]) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM scripts")
        await db.executemany("INSERT INTO scripts VALUES (?, ?)", scripts.items())
        await db.commit()
