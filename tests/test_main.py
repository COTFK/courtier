import pytest

from courtier.app import main
from courtier.settings import SETTINGS


def test_main_serves_the_app_on_all_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        "uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    main()

    [(args, kwargs)] = calls
    assert args == ("courtier.app:app",)
    assert kwargs == {
        "host": "0.0.0.0",  # noqa: S104
        "port": SETTINGS.port,
        "reload": SETTINGS.hot_reload,
    }
