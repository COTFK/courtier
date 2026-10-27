# Courtier - an aggregator for Yu-Gi-Oh assets

Courtier is an HTTP server & JSON API that **downloads, caches and mirrors** Yu-Gi-Oh! card data for use in other applications.

The goal is to serve as a middleware layer for future EDOPro-based simulator clients (i.e. [Project Tensho]) or any software that needs access to Yu-Gi-Oh! card images and/or data.

Courtier aggregates the following:
- Card data from [Yaml Yugi]
- Card images from [YGOPRODeck]
- Lua card scripts from [Project Ignis: EDOPro]

## Getting started

For local testing with Docker and Docker Compose:

```sh
docker compose up --build
```

The API is available at `http://localhost:8000`. Compose starts a local S3 emulator with development-only credentials and persists both SQLite and image data in named volumes. Set `COURTIER_PORT` to change the host port. No `.env` file is required for Compose.

To run without Compose, you need Python 3.14 or later, [uv], and an existing S3-compatible bucket with credentials that can list, read, and write objects. Copy the template, then configure the required `S3_*` variables:

```sh
cp .env.example .env
uv sync --locked
uv run --no-sync courtier
```

See [.env.example](.env.example) for optional settings. Set `S3_ADDRESSING_STYLE=path` if your storage provider requires path-style addressing.

Synchronization starts in the background. A new cache may return 404 responses until the initial sync completes; check the server logs for progress or errors.

The API has no authentication. Restrict network access or put it behind an authenticating proxy if needed.

## API

Interactive API documentation is available at `/docs`. A [Bruno] collection is in [docs/API](docs/API).

Here's the gist of it:

- Get card data:
    - for one card: `GET /v1/cards/{card_password}`
    - for up to 100 cards: `GET /v1/cards?ids={card_password}&ids={card_password}&ids={card_password}`
- Get card image: 
    - Full size: `GET /v1/images/{card_password}`
    - Thumbnail: `GET /v1/images/{card_password}/thumbnail`
- Get card script:
    - for one card: `GET /v1/scripts/cards/{card_password}`
    - for up to 100 cards: `GET /v1/scripts/cards?ids={card_password}&ids={card_password}`
- Get EDOPro system scripts: 
    - Get the list of filenames: `GET /v1/scripts/system/files`
    - Get one by its filename: `GET /v1/scripts/system/utility.lua`
    - Get several or all at once: `GET /v1/scripts/system?file={filename}&file={filename}`

## Development

Install the development dependencies with `uv sync --locked --dev`, then run the same checks as CI:

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest --cov=courtier --cov-branch --cov-report=term-missing
```

Issues and pull requests are welcome. Tests use isolated local storage and mocked upstream requests; they do not require S3 credentials.

## License

Courtier is licensed under the [GNU Affero General Public License v3.0 or later](LICENSE). Upstream data, images, and scripts may be subject to their own licenses or terms.

[Yaml Yugi]: https://github.com/DawnbrandBots/yaml-yugi
[YGOPRODeck]: https://ygoprodeck.com/
[Project Ignis: EDOPro]: https://github.com/ProjectIgnis/CardScripts
[Bruno]: https://www.usebruno.com/
[uv]: https://docs.astral.sh/uv/
[Project Tensho]: https://github.com/COTFK/project-tensho