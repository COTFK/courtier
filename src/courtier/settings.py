from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", hide_input_in_errors=True)

    hot_reload: bool = False
    port: int = 8000
    database_path: Path = Path("cache.db")

    yaml_yugi_root: str = "https://dawnbrandbots.github.io/yaml-yugi"
    ygoprodeck_root: str = "https://db.ygoprodeck.com/api/v7"

    sync_cooldown: timedelta = timedelta(hours=12)
    """Minimum time between full syncs, as an ISO 8601 duration, e.g. `PT12H`."""

    card_scripts_archive: str = (
        "https://codeload.github.com/ProjectIgnis/CardScripts/tar.gz/refs/heads/master"
    )

    card_script_folders: list[str] = ["official", "pre-release"]
    """Search order, configured as a JSON list, e.g. `["official"]`."""

    card_images_root: str = "https://images.ygoprodeck.com/images/cards"
    card_thumbnails_root: str = "https://images.ygoprodeck.com/images/cards_small"

    card_image_concurrency: int = Field(default=8, ge=1)

    s3_bucket: str = Field(min_length=1)
    s3_endpoint: HttpUrl
    s3_region: str = Field(min_length=1, default="auto")
    s3_access_key_id: SecretStr = Field(min_length=1)
    s3_secret_access_key: SecretStr = Field(min_length=1)

    s3_addressing_style: Literal["virtual", "path"] = "virtual"
    s3_allow_http: bool = False


SETTINGS = Settings()
