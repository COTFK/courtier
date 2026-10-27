import io
import tarfile
from collections.abc import Callable

import httpx2 as httpx

from courtier.models.card import Card

HttpHandler = Callable[[httpx.Request], httpx.Response]


def make_archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f"CardScripts-master/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def make_card(card_id: str = "89631139", name: str = "Blue-Eyes White Dragon") -> Card:
    return Card(
        id=card_id,
        name=name,
        type="Monster",
        race="Dragon",
        level=8,
        atk="3000",
        def_="2500",
        labels={"password": card_id, "name": {"en": name}},
    )
