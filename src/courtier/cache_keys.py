"""Names under which card scripts and images are stored, shared by providers, sync and routes."""

ROOT_SCRIPTS = (
    "archetype_setcode_constants.lua",
    "card_counter_constants.lua",
    "cards_specific_functions.lua",
    "chain.lua",
    "constant.lua",
    "debug_utility.lua",
    "deprecated_functions.lua",
    "proc_equip.lua",
    "proc_fusion.lua",
    "proc_fusion_spell.lua",
    "proc_gemini.lua",
    "proc_link.lua",
    "proc_maximum.lua",
    "proc_normal.lua",
    "proc_pendulum.lua",
    "proc_persistent.lua",
    "proc_ritual.lua",
    "proc_rush.lua",
    "proc_skill.lua",
    "proc_spirit.lua",
    "proc_synchro.lua",
    "proc_union.lua",
    "proc_unofficial.lua",
    "proc_workaround.lua",
    "proc_xyz.lua",
    "utility.lua",
)
"""Shared scripts that card scripts depend on."""


def card_script_name(card_id: str) -> str:
    """Card scripts are named by their unpadded passcode, e.g. `c423705.lua` for `00423705`."""
    return f"c{int(card_id)}.lua"


def card_image_key(card_id: str) -> str:
    """Images are named by their unpadded passcode, e.g. `423705.jpg` for `00423705`, as YGOPRODeck does."""
    return f"{int(card_id)}.jpg"


def card_thumbnail_key(card_id: str) -> str:
    return f"thumbnails/{card_image_key(card_id)}"
