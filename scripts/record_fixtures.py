"""Record the API answers the tests replay, from production, trimmed to a few varied markets.

    uv run python scripts/record_fixtures.py

Run it when the API gains a field the tests should cover, then check the diff by eye. The
forecast record's ``*.spec.*`` files are the examples from its published spec and are kept by
hand: production's own board and index are recorded beside them, empty until forecasts close.
"""

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = "https://api.usehyperodds.com"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

http = httpx.Client(base_url=API, timeout=30, headers={"user-agent": "hyperodds-python fixtures"})


def get(path: str, input: Any = None, **params: str) -> httpx.Response:
    if input is not None:
        params["input"] = json.dumps(input, separators=(",", ":"))
    return http.get(f"/trpc/{path}", params=params)


def data(path: str, input: Any = None) -> Any:
    response = get(path, input)
    response.raise_for_status()
    return response.json()["result"]["data"]


def rest(path: str, **params: str) -> httpx.Response:
    response = http.get(path, params=params)
    response.raise_for_status()
    return response


def save(name: str, body: Any) -> None:
    (OUT / name).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", name)


def envelope(value: Any) -> dict[str, Any]:
    return {"result": {"data": value}}


def trim_book(book: dict[str, Any] | None, n: int = 4) -> dict[str, Any] | None:
    if book is None:
        return None
    return {**book, "bids": book["bids"][:n], "asks": book["asks"][:n]}


def pick(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One market for each shape the models have to read."""
    tests = [
        lambda m: (
            m["fair"] and m["fair"]["method"] == "options" and m["edge"] and m["edge"]["best"]
        ),
        lambda m: m["fair"] and m["fair"]["method"] == "consensus" and m["matchGrade"] == "SAME",
        lambda m: m["matchGrade"] == "CLOSE" and m["fair"] is None,
        lambda m: m["role"] == "fallback",
        lambda m: m["role"] == "named",
        lambda m: m["deployer"] == "hl",
        lambda m: m["eventTime"] is None,
        lambda m: m["listedAt"] is None,
        lambda m: m["edge"] is None,
        lambda m: m["kind"].startswith("price") and m["deadline"] in (None, m["eventTime"]),
        lambda m: m["kind"].startswith("price") and m["deadline"] not in (None, m["eventTime"]),
    ]
    chosen: dict[int, dict[str, Any]] = {}
    for test in tests:
        found = next((m for m in markets if test(m) and m["outcome"] not in chosen), None)
        if found is None:
            sys.exit(f"no live market fits {test.__code__.co_firstlineno}; widen the picks")
        chosen[found["outcome"]] = found
    return list(chosen.values())


def detail(outcome: int) -> dict[str, Any]:
    d = data("markets.get", {"outcome": outcome})
    d["book"] = trim_book(d["book"])
    d["trades"] = d["trades"][:3]
    for m in d["matches"]:
        m["book"] = trim_book(m["book"])
    if d["question"]:
        d["question"]["outcomes"] = d["question"]["outcomes"][:3]
    if d["cluster"]:
        for key in ("mergedBook", "mergedBookWithClose"):
            d["cluster"][key] = trim_book(d["cluster"][key])
    return d


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    markets = data("markets.list")
    picked = pick(markets)
    # Clusters come and go with listings; with none live, the answers recorded before stay.
    clustered = next((m for m in markets if m["clusterId"]), None)
    if clustered and clustered not in picked:
        picked.append(clustered)
    save("markets.list.json", envelope(picked))

    by_kind = {
        "matched": next(m for m in picked if m["matchGrade"] == "SAME"),
        "options": next(m for m in picked if m["fair"] and m["fair"]["method"] == "options"),
        "named": next(m for m in picked if m["role"] == "named"),
    }
    if clustered:
        by_kind["cluster"] = clustered
    else:
        print("no live cluster: kept the recorded cluster answers")
    for kind, market in by_kind.items():
        save(f"markets.get.{kind}.json", envelope(detail(market["outcome"])))

    outcomes = [by_kind["matched"]["outcome"], 1, by_kind["options"]["outcome"]]
    batch = get(
        ",".join(["markets.get"] * len(outcomes)),
        {str(i): {"outcome": o} for i, o in enumerate(outcomes)},
        batch="1",
    )
    entries = batch.json()
    for entry in entries:
        if "result" in entry:
            entry["result"]["data"] = detail(entry["result"]["data"]["summary"]["outcome"])
    save(
        "markets.get.batch.json",
        {"status": batch.status_code, "outcomes": outcomes, "body": entries},
    )

    missing = get("markets.get", {"outcome": 1})
    save("error.not-found.json", {"status": missing.status_code, "body": missing.json()})

    save("markets.facets.json", envelope(data("markets.facets")))
    save("markets.newest.json", envelope(data("markets.newest", {"limit": 3})))
    save("markets.edges.json", envelope(data("markets.edges", {"limit": 3})))
    save("markets.book.json", envelope(trim_book(data("markets.book", {"outcome": outcomes[0]}))))
    trades = data("markets.trades", {"outcome": outcomes[0], "limit": 5})
    save("markets.trades.json", envelope(trades))

    questions = data("questions.list")
    for q in questions:
        q["outcomes"] = q["outcomes"][:3]
    save("questions.list.json", envelope(questions[:3]))
    question = data("questions.get", {"question": by_kind["named"]["questionId"]})
    save("questions.get.json", envelope(question))

    if clustered:
        save("clusters.list.json", envelope(data("clusters.list")[:2]))
        cluster = data("clusters.get", {"id": clustered["clusterId"]})
        for key in ("mergedBook", "mergedBookWithClose"):
            cluster[key] = trim_book(cluster[key])
        save("clusters.get.json", envelope(cluster))

    compare = data(
        "quotes.compare",
        {"outcome": outcomes[0], "side": 0, "action": "buy", "shares": 100},
    )
    save("quotes.compare.json", envelope(compare))
    save("meta.status.json", envelope(data("meta.status")))

    matched = outcomes[0]
    history = data("markets.history", {"outcome": matched, "every": 3_600_000})
    save("markets.history.json", envelope(history[:2] + history[-2:]))
    since = int((time.time() - 3 * 86_400) * 1000)
    venues = data("markets.venueHistory", {"outcome": matched, "from": since, "step": 240})
    for line in venues["lines"]:
        line["prints"] = line["prints"][:3]
    save("markets.venueHistory.json", envelope(venues))

    (OUT / "rest").mkdir(exist_ok=True)
    bars = rest(
        "/udf/history",
        symbol=f"HIP4:{matched * 10}",
        resolution="1D",
        countback="12",
        to=str(int(time.time())),
    )
    save("rest/udf.history.json", bars.json())
    sheet = rest("/sheets/markets.csv", limit="3", sort="settling")
    (OUT / "rest" / "sheets.markets.csv").write_text(sheet.text, encoding="utf-8")
    print("wrote rest/sheets.markets.csv")
    save("rest/forecasts.board.json", rest("/forecasts/board").json())
    save("rest/forecasts.records.json", rest("/forecasts/records").json())

    newest = max(m["outcome"] for m in markets)
    with http.stream(
        "GET",
        "/trpc/live.listings",
        params={"input": json.dumps({"lastEventId": str(newest - 3)})},
        headers={"accept": "text/event-stream"},
    ) as stream:
        lines: list[str] = []
        for line in stream.iter_lines():
            if line.startswith("data: ["):
                line = "data: " + json.dumps(json.loads(line[6:])[:2], separators=(",", ":"))
            lines.append(line)
            if line.startswith("id:"):
                break
    (OUT / "live.listings.sse").write_text("\n".join(lines) + "\n\n", encoding="utf-8")
    print("wrote live.listings.sse")


if __name__ == "__main__":
    main()
