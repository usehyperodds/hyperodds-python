import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import TypeAdapter

from hyperodds import (
    Cluster,
    ClusterList,
    Comparison,
    Facets,
    ForecastBoard,
    History,
    MarketDetail,
    MarketList,
    Question,
    QuestionList,
    RecordDayList,
    RecordLineList,
    Status,
    TradeList,
    VenueHistory,
)
from hyperodds._client import _sse_events

from .conftest import FIXTURES, data, load, text, unnamed

SHAPES = {
    "markets.list.json": MarketList,
    "markets.newest.json": MarketList,
    "markets.edges.json": MarketList,
    "markets.get.matched.json": MarketDetail,
    "markets.get.options.json": MarketDetail,
    "markets.get.cluster.json": MarketDetail,
    "markets.get.named.json": MarketDetail,
    "markets.trades.json": TradeList,
    "markets.facets.json": Facets,
    "markets.history.json": History,
    "markets.venueHistory.json": VenueHistory,
    "questions.list.json": QuestionList,
    "questions.get.json": Question,
    "clusters.list.json": ClusterList,
    "clusters.get.json": Cluster,
    "quotes.compare.json": Comparison,
    "meta.status.json": Status,
}


def test_every_recorded_answer_has_a_shape() -> None:
    recorded = {p.name for p in FIXTURES.glob("*.json")}
    assert recorded - set(SHAPES) == {
        "markets.book.json",
        "markets.get.batch.json",
        "error.not-found.json",
    }


@pytest.mark.parametrize(("name", "shape"), SHAPES.items())
def test_recorded_answers_read_without_loss(name: str, shape: type) -> None:
    value: Any = TypeAdapter(shape).validate_python(data(name))
    assert unnamed(value) == [], name


def test_lists_keep_their_type_inside_models() -> None:
    detail = MarketDetail.model_validate(data("markets.get.matched.json"))
    assert isinstance(detail.trades, TradeList)
    question = Question.model_validate(data("questions.get.json"))
    assert isinstance(question.outcomes, MarketList)


def test_times_are_utc() -> None:
    detail = MarketDetail.model_validate(data("markets.get.options.json"))
    assert detail.fair is not None
    assert detail.fair.ts is not None
    assert detail.fair.ts.tzinfo is timezone.utc
    assert detail.trades[0].at.tzinfo is timezone.utc
    assert all(m.book_ts is None or m.book_ts.tzinfo is timezone.utc for m in detail.matches)


T = datetime(2026, 11, 23, 18, tzinfo=timezone.utc)
LATER = T + timedelta(days=7)


@pytest.mark.parametrize(
    ("kind", "event_time", "deadline", "expected"),
    [
        ("contest_winner", T, LATER, (T, None, LATER)),
        ("policy_rate", T, LATER, (T, None, LATER)),
        ("contest_result", None, LATER, (None, None, LATER)),
        ("price_binary", T, None, (None, T, None)),
        ("price_bucket", T, T, (None, T, None)),
        ("price_touch", T, LATER, (None, None, LATER)),
        ("price_touch", None, LATER, (None, None, LATER)),
        ("price_binary", None, None, (None, None, None)),
    ],
)  # fmt: skip
def test_times_follow_the_console(
    kind: str, event_time: datetime | None, deadline: datetime | None, expected: tuple[Any, ...]
) -> None:
    (row, *_) = TypeAdapter(MarketList).validate_python(data("markets.list.json"))
    market = row.model_copy(
        update={"kind": kind, "event_time": event_time, "deadline": deadline, "timing": None}
    )
    assert (market.starts, market.settles_at, market.settles_by) == expected


def test_times_are_the_ones_the_api_sends() -> None:
    markets = TypeAdapter(MarketList).validate_python(data("markets.list.json"))
    for m in markets:
        assert m.timing is not None
        sent = (m.timing.starts, m.timing.settles, m.timing.by)
        assert (m.starts, m.settles_at, m.settles_by) == sent
        # A question carries no timing of its own, so the rule it falls back on has to agree.
        ruled = m.model_copy(update={"timing": None})
        assert (ruled.starts, ruled.settles_at, ruled.settles_by) == sent


def test_recorded_markets_have_one_settlement_time() -> None:
    markets = TypeAdapter(MarketList).validate_python(data("markets.list.json"))
    assert {m.kind for m in markets} & {"price_binary", "price_bucket", "price_touch"}
    for m in markets:
        assert m.settles_at is None or m.settles_by is None
        if m.kind.startswith("price"):
            assert m.starts is None
        else:
            assert (m.starts, m.settles_by) == (m.event_time, m.deadline)


def test_a_touch_market_settles_by_its_deadline() -> None:
    markets = TypeAdapter(MarketList).validate_python(data("markets.list.json"))
    touch = next(m for m in markets if m.kind == "price_touch")
    assert (touch.starts, touch.settles_at, touch.settles_by) == (None, None, touch.deadline)
    assert touch.deadline is not None


def test_a_market_in_full_has_the_summary_times_and_url() -> None:
    detail = MarketDetail.model_validate(data("markets.get.matched.json"))
    s = detail.summary
    assert (detail.starts, detail.settles_at, detail.settles_by) == (
        s.starts,
        s.settles_at,
        s.settles_by,
    )
    assert detail.url == s.url == f"https://usehyperodds.com/m/{s.outcome}"


@pytest.mark.parametrize("name", ["forecasts.board.json", "forecasts.board.spec.json"])
def test_the_forecast_board_reads_without_loss(name: str) -> None:
    board = ForecastBoard.model_validate(load(f"rest/{name}"))
    assert unnamed(board) == []
    assert [b.key for b in board.baselines] == ["fair", "market"]
    assert len(board.rows) <= board.total


def test_the_spec_board_ranks_forecasters() -> None:
    board = ForecastBoard.model_validate(load("rest/forecasts.board.spec.json"))
    assert board.as_of == datetime(2025, 11, 23, 0, 5, tzinfo=timezone.utc)
    assert board.through == date(2025, 11, 22)
    assert [(r.rank, r.early) for r in board.rows] == [(1, False), (2, True)]
    assert board.rows[0].skill_fair_low == 0.001874


@pytest.mark.parametrize("name", ["forecasts.records.json", "forecasts.records.spec.json"])
def test_the_record_index_reads_without_loss(name: str) -> None:
    days = TypeAdapter(RecordDayList).validate_python(load(f"rest/{name}")["days"])
    assert unnamed(days) == []
    assert all(d.url.endswith(f"/forecasts/records/{d.day}.jsonl") for d in days)


def test_a_day_of_the_record_reads_without_loss() -> None:
    raw = [json.loads(line) for line in text("rest/forecasts.day.spec.jsonl").splitlines()]
    lines = TypeAdapter(RecordLineList).validate_python(raw)
    assert unnamed(lines) == []
    first, second = lines
    assert first.probability_bps == 6200
    assert first.accepted_at == datetime(2025, 11, 21, 10, 0, 1, 234000, tzinfo=timezone.utc)
    assert (second.fair_bps, second.mid_bps, second.book_time) == (None, None, None)
    assert [line.model_dump(by_alias=True, mode="json").keys() for line in lines] == [
        r.keys() for r in raw
    ]


def test_matches_say_whether_the_venue_market_trades() -> None:
    detail = MarketDetail.model_validate(data("markets.get.matched.json"))
    assert detail.matches
    assert all(isinstance(m.trading, bool) for m in detail.matches)
    assert "trading" in data("markets.get.matched.json")["matches"][0]


def test_the_hip4_quote_carries_the_settlement_fee() -> None:
    hl = Comparison.model_validate(data("quotes.compare.json")).hl
    assert hl.settlement_fee_rate is not None
    assert hl.all_in_px is not None
    assert hl.held_all_in_px is not None
    assert hl.held_all_in_px == pytest.approx(hl.all_in_px / (1 - hl.settlement_fee_rate), abs=1e-6)


def test_fair_line_sources_come_with_markets_get() -> None:
    detail = MarketDetail.model_validate(data("markets.get.options.json"))
    assert detail.fair is not None
    assert detail.fair.sources
    assert detail.summary.fair is not None
    assert detail.summary.fair.sources is None


def test_every_result_model_is_exported() -> None:
    import hyperodds
    from hyperodds import models

    defined = {
        name
        for name, value in vars(models).items()
        if isinstance(value, type) and value.__module__ == models.__name__ and name[0] != "_"
    }
    assert defined - {"Model"} <= set(hyperodds.__all__)


def test_models_are_frozen() -> None:
    (market, *_) = TypeAdapter(MarketList).validate_python(data("markets.list.json"))
    with pytest.raises(ValueError, match="frozen"):
        market.title = "x"  # type: ignore[misc]


def test_dump_uses_the_api_names() -> None:
    (market, *_) = TypeAdapter(MarketList).validate_python(data("markets.list.json"))
    dumped = market.model_dump(by_alias=True, mode="json")
    assert set(dumped) == set(data("markets.list.json")[0])


def test_sse_parser() -> None:
    lines = [
        "event: connected",
        'data: {"reconnectAfterInactivityMs":45000}',
        "",
        ": a comment",
        "event: ping",
        "",
        "data: [1,",
        "data: 2]",
        "id: 7",
        "",
        "id: 8",
        "data:[3]",
    ]
    assert list(_sse_events(lines)) == [
        ("connected", '{"reconnectAfterInactivityMs":45000}', ""),
        ("message", "[1,\n2]", "7"),
        ("message", "[3]", "8"),
    ]
