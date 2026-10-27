"""Pydantic data models representing Yu-Gi-Oh! cards and their properties."""

from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, StringConstraints


def normalize_card_id(value: int | str) -> str:
    """Pad a card ID to the 8 digits it is stored under, e.g. `423705` becomes `00423705`."""
    return str(value).zfill(8)


CardID = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]+$"),
    AfterValidator(normalize_card_id),
]
"""A numeric card password, padded to at least eight digits."""


class Card(BaseModel):
    """A Yu-Gi-Oh! card with all its properties."""

    id: CardID
    name: str
    archetype: list[str] | None = None
    type: str
    race: str | None = None
    subtypes: list[str] | None = None
    level: int | None = None
    rank: int | None = None
    attribute: str | None = None
    atk: str | None = Field(default=None, description="ATK, or `?` if variable.")
    def_: str | None = Field(
        default=None, serialization_alias="def", description="DEF, or `?` if variable."
    )
    scale: int | None = None
    linkval: int | None = None
    linkmarkers: list[int] | None = Field(
        default=None,
        description="Sorted numeric keypad directions: 1 is bottom-left, 9 is top-right.",
    )
    labels: dict[str, Any] | None = Field(
        default=None,
        description="The unmodified Yaml Yugi record.",
    )
