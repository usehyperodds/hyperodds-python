import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from hyperodds import Hyperodds

FIXTURES = Path(__file__).parent / "fixtures"

Handler = Callable[[httpx.Request], httpx.Response]
MakeClient = Callable[..., Hyperodds]


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def data(name: str) -> Any:
    return load(name)["result"]["data"]


def procedure(request: httpx.Request) -> str:
    return request.url.path.removeprefix("/trpc/")


def sent_input(request: httpx.Request) -> Any:
    raw = request.url.params.get("input")
    return None if raw is None else json.loads(raw)


def unnamed(value: Any, path: str = "") -> list[str]:
    """Every field the models do not name, anywhere inside a result, with where it was found."""
    if isinstance(value, BaseModel):
        found: list[str] = []
        if value.model_extra:
            found.append(f"{path or type(value).__name__}: {sorted(value.model_extra)}")
        for name in type(value).model_fields:
            found += unnamed(getattr(value, name), f"{path}.{name}")
        return found
    if isinstance(value, (list, tuple)):
        return [f for i, item in enumerate(value) for f in unnamed(item, f"{path}[{i}]")]
    if isinstance(value, dict):
        return [f for k, item in value.items() for f in unnamed(item, f"{path}[{k!r}]")]
    return []


REST = {
    "/udf/history": ("rest/udf.history.json", "application/json"),
    "/sheets/markets.csv": ("rest/sheets.markets.csv", "text/csv; charset=utf-8"),
    "/forecasts/board": ("rest/forecasts.board.spec.json", "application/json"),
    "/forecasts/records": ("rest/forecasts.records.spec.json", "application/json"),
    "/forecasts/records/2025-11-22.jsonl": (
        "rest/forecasts.day.spec.jsonl",
        "application/x-ndjson; charset=utf-8",
    ),
}


def recorded(request: httpx.Request) -> httpx.Response:
    """Answer each procedure, and each path outside tRPC, with its recorded response."""
    if request.url.path in REST:
        name, kind = REST[request.url.path]
        return httpx.Response(200, text=text(name), headers={"content-type": kind})
    path = procedure(request)
    name = "markets.get.matched.json" if path == "markets.get" else f"{path}.json"
    return httpx.Response(200, json=load(name))


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("hyperodds._client.time.sleep", slept.append)
    return slept


@pytest.fixture
def make_client(sleeps: list[float]) -> Iterator[MakeClient]:
    opened: list[httpx.Client] = []

    def make(handler: Handler = recorded, **options: Any) -> Hyperodds:
        http = httpx.Client(transport=httpx.MockTransport(handler))
        opened.append(http)
        options.setdefault("rate_limit", None)
        return Hyperodds("https://api.test", http_client=http, **options)

    yield make
    for http in opened:
        http.close()


@pytest.fixture
def requests() -> list[httpx.Request]:
    return []
