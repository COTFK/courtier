import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, Response
from obstore.store import S3Store
from starlette.middleware.base import RequestResponseEndpoint

from courtier.database import initialize_db
from courtier.providers.bucket import create_bucket_client
from courtier.routes.get_card_data import router as card_data_router
from courtier.routes.get_card_images import router as card_images_router
from courtier.routes.get_scripts import router as scripts_router
from courtier.settings import SETTINGS
from courtier.sync import sync_periodically


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[dict[str, S3Store]]:
    await initialize_db()

    bucket = create_bucket_client()
    sync_task = asyncio.create_task(sync_periodically(bucket))

    try:
        yield {"bucket": bucket}
    finally:
        sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await sync_task


app = FastAPI(
    title="Courtier",
    version="1",
    description="Caches Yu-Gi-Oh! card data, images, and Lua scripts.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
app.add_middleware(GZipMiddleware)
app.include_router(card_data_router, prefix="/v1")
app.include_router(card_images_router, prefix="/v1")
app.include_router(scripts_router, prefix="/v1")


@app.middleware("http")
async def prevent_error_caching(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    response = await call_next(request)
    if response.status_code >= 400:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def handle_unexpected_error(
    _request: Request, _exception: Exception
) -> PlainTextResponse:
    return PlainTextResponse(
        "Internal Server Error",
        status_code=500,
        headers={"Cache-Control": "no-store"},
    )


def main() -> None:
    uvicorn.run(
        "courtier.app:app",
        host="0.0.0.0",  # noqa: S104
        port=SETTINGS.port,
        reload=SETTINGS.hot_reload,
    )
