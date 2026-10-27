"""Request parameters and dependencies shared by routes."""

from collections.abc import Iterable, Sequence
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from obstore.store import ObjectStore

from courtier.models.card import CardID


def get_bucket(request: Request) -> ObjectStore:
    return request.state.bucket


def raise_for_missing(
    kind: str, requested: Sequence[str], known: Iterable[str]
) -> None:
    if missing := set(requested) - set(known):
        raise HTTPException(
            status_code=404,
            detail=f"{kind} not found: {', '.join(sorted(missing))}.",
        )


Bucket = Annotated[ObjectStore, Depends(get_bucket)]

CardIDs = Annotated[
    list[CardID],
    Query(
        min_length=1,
        max_length=100,
        description="Card IDs to look up, repeated as `ids=89631139&ids=423705`. At most 100.",
        examples=[["89631139", "423705"]],
    ),
]
