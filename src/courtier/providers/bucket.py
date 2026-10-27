"""Async S3-compatible bucket client construction for card image storage."""

from datetime import timedelta

from obstore.store import S3Store

from courtier.settings import SETTINGS


def _bucket_endpoint() -> str:
    endpoint = str(SETTINGS.s3_endpoint).rstrip("/")
    if SETTINGS.s3_addressing_style == "path":
        return endpoint
    # obstore only builds virtual-hosted requests when the bucket name is already in the endpoint host.
    return endpoint.replace("://", f"://{SETTINGS.s3_bucket}.", 1)


def create_bucket_client() -> S3Store:
    return S3Store(
        SETTINGS.s3_bucket,
        endpoint=_bucket_endpoint(),
        region=SETTINGS.s3_region,
        access_key_id=SETTINGS.s3_access_key_id.get_secret_value(),
        secret_access_key=SETTINGS.s3_secret_access_key.get_secret_value(),
        virtual_hosted_style_request=SETTINGS.s3_addressing_style == "virtual",
        client_options={
            "allow_http": SETTINGS.s3_allow_http,
            "connect_timeout": timedelta(seconds=5),
            "timeout": timedelta(seconds=30),
        },
        # max_retries counts retries after the first attempt, so this allows three attempts in total.
        retry_config={"max_retries": 2},
    )
