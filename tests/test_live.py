"""Smoke tests against production. Run them with ``pytest -m live``.

They read every live market once, so they also catch an API change the recorded answers predate.
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from itertools import islice

import pytest

from hyperodds import Hyperodds, Market, MarketList, NotFoundError

from .conftest import unnamed

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def ho() -> Iterator[Hyperodds]:
    with Hyperodds() as client:
        yield client


@pytest.fixture(scope="module")
def markets(ho: Hyperodds) -> MarketList:
    return ho.markets.list()


@pytest.fixture(scope="module")
def matched(markets: MarketList) -> Market:
    """The busiest market with a Polymarket or Kalshi match, which has a history on every read."""
    return max(
        (m for m in markets if m.match_grade in ("SAME", "CLOSE")), key=lambda m: m.volume_24h
    )


def test_ready(ho: Hyperodds) -> None:
    status = ho.meta.status()
    assert status.ready
    assert status.markets > 0
    assert unnamed(status) == []


def test_times_are_the_ones_the_api_sends(markets: MarketList) -> None:
    for m in markets:
        assert m.timing is not None
        sent = (m.timing.starts, m.timing.settles, m.timing.by)
        assert (m.starts, m.settles_at, m.settles_by) == sent
        ruled = m.model_copy(update={"timing": None})
        assert (ruled.starts, ruled.settles_at, ruled.settles_by) == sent, m.outcome


def test_every_live_market_reads(ho: Hyperodds, markets: MarketList) -> None:
    assert len(markets) > 0
    df = markets.to_dataframe()
    assert len(df) == len(markets)
    assert df.index.is_unique
    facets = ho.markets.facets()
    assert abs(facets.total - len(markets)) <= 10
    assert unnamed(markets) == []


def test_filters(ho: Hyperodds, markets: MarketList) -> None:
    category = markets[0].category
    filtered = ho.markets.list(category=category, sort="new", limit=5)
    assert 0 < len(filtered) <= 5
    assert {m.category for m in filtered} == {category}
    outcomes = [m.outcome for m in filtered]
    assert outcomes == sorted(outcomes, reverse=True)


def test_matched_markets_in_full(ho: Hyperodds, markets: MarketList) -> None:
    matched = [m.outcome for m in markets if m.match_grade in ("SAME", "CLOSE")][:12]
    details = ho.markets.get_many(matched)
    assert len(details) >= len(matched) - 2
    assert unnamed(details) == []
    df = details.matches_dataframe()
    assert len(df) > 0
    assert set(df.venue) <= {"pm", "kx"}

    outcome = next(iter(details))
    comparison = ho.quotes.compare(outcome, side=0, action="buy", shares=100)
    assert unnamed(comparison) == []
    assert comparison.hl.settlement_fee_rate is not None
    assert comparison.to_dataframe().venue.iloc[0] == "hl"


def test_book_and_trades(ho: Hyperodds, markets: MarketList) -> None:
    busiest = max(markets, key=lambda m: m.volume_24h)
    book = ho.markets.book(busiest.outcome)
    assert book is not None
    assert book.bids or book.asks
    trades = ho.markets.trades(busiest.outcome, limit=5)
    assert len(trades) <= 5


def test_questions_clusters_newest_edges(ho: Hyperodds) -> None:
    questions = ho.questions.list()
    assert unnamed(questions) == []
    if questions:
        first = ho.questions.get(questions[0].question)
        assert first.question == questions[0].question
        assert len(questions.to_dataframe()) == len(questions)
    clusters = ho.clusters.list()
    if clusters:
        assert ho.clusters.get(clusters[0].id).members
    assert len(ho.markets.newest(limit=3)) <= 3
    for market in ho.markets.edges(limit=5):
        assert market.edge is not None
        assert market.edge.best is not None


def test_missing_market(ho: Hyperodds) -> None:
    with pytest.raises(NotFoundError):
        ho.markets.get(1)


def test_listings_resume_from_an_earlier_listing(ho: Hyperodds) -> None:
    newest = ho.markets.newest(limit=3)
    since = min(m.outcome for m in newest) - 1
    stream = ho.live.listings(last_event_id=since)
    (first,) = islice(stream, 1)
    stream.close()
    assert {m.outcome for m in newest} <= {m.outcome for m in first} | {since}


def test_history_of_the_last_day(ho: Hyperodds, matched: Market) -> None:
    now = datetime.now(timezone.utc)
    points = ho.markets.history(matched.outcome, every=timedelta(minutes=30))
    assert unnamed(points) == []
    assert 40 <= len(points) <= 50
    times = [p.at for p in points]
    assert times == sorted(times)
    assert now - timedelta(hours=25) <= times[0]
    assert times[-1] <= now + timedelta(minutes=1)
    assert points[-1].last_px is not None
    df = points.to_dataframe()
    assert len(df) == len(points)
    assert df.last_px.dropna().between(0, 1).all()


def test_venue_history_beside_a_matched_market(ho: Hyperodds, matched: Market) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=2)
    venues = ho.markets.venue_history(matched.outcome, since, step=60)
    assert unnamed(venues) == []
    assert venues.step == 60
    assert venues.since <= since
    assert venues.lines
    assert {line.venue for line in venues.lines} <= {"pm", "kx"}
    assert {line.state for line in venues.lines} <= {"ok", "empty", "unavailable"}
    for line in venues.lines:
        assert all(0 <= p.p <= 1 for p in line.prints)
    df = venues.to_dataframe()
    assert len(df) == sum(len(line.prints) for line in venues.lines)


def test_bars_from_minutes_to_months(ho: Hyperodds, matched: Market) -> None:
    days = ho.charts.bars(matched.outcome, "1d", limit=10)
    assert 0 < len(days) <= 10
    assert all(b.at.hour == b.at.minute == 0 for b in days)
    assert all(0 <= b.low <= min(b.open, b.close) for b in days)
    assert all(max(b.open, b.close) <= b.high <= 1 for b in days)
    assert [b.at for b in days] == sorted(b.at for b in days)

    other = ho.charts.bars(matched.outcome, "1d", side=1, limit=10)
    assert [b.at for b in other] == [b.at for b in days]
    assert other[0].open == pytest.approx(1 - days[0].open, abs=1e-5)
    assert other[0].high == pytest.approx(1 - days[0].low, abs=1e-5)

    since = datetime.now(timezone.utc) - timedelta(days=2)
    hours = ho.charts.bars(matched.outcome, "1h", start=since)
    assert 0 < len(hours) <= 49
    assert hours[0].at >= since - timedelta(hours=1)
    assert hours[-1].close == pytest.approx(matched.last_px, abs=0.05)

    everything = ho.charts.bars(matched.outcome, "1d")
    weeks = ho.charts.bars(matched.outcome, "1w")
    months = ho.charts.bars(matched.outcome, "1M")
    assert all(b.at.weekday() == 0 for b in weeks)
    assert all(b.at.day == 1 for b in months)
    assert len(months) <= len(weeks) <= len(everything)
    total = sum(b.volume for b in everything)
    assert sum(b.volume for b in weeks) == pytest.approx(total, rel=0.01)
    assert sum(b.volume for b in months) == pytest.approx(total, rel=0.01)
    assert len(ho.charts.bars(matched.outcome, "1m", limit=3)) == 3
    assert len(days.to_dataframe()) == len(days)

    with pytest.raises(NotFoundError):
        ho.charts.bars(1)


def test_sheets_are_the_markets(ho: Hyperodds, markets: MarketList, matched: Market) -> None:
    df = ho.sheets.markets(limit=25).to_dataframe()
    assert 0 < len(df) <= 25
    # A market can list or settle between the two reads.
    assert len(set(df.outcome) - {m.outcome for m in markets}) <= 2
    assert df.mid.dropna().between(0, 1).all()
    assert str(df.settles_by_utc.dt.tz) == "UTC"

    one = ho.sheets.market(matched.outcome, columns=["outcome", "status"], header=False)
    assert one.strip() == f"{matched.outcome},{matched.status}"
    edges = ho.sheets.edges(limit=5, columns=["outcome", "edge_pts"]).to_dataframe()
    assert list(edges.columns) == ["outcome", "edge_pts"]
    assert len(edges) <= 5
    question = next(m.question_id for m in markets if m.question_id is not None)
    labels = ho.sheets.question(question, columns=["label"]).to_dataframe()
    assert len(labels) > 0

    with pytest.raises(NotFoundError):
        ho.sheets.market(1)


def test_the_forecast_record(ho: Hyperodds) -> None:
    board = ho.forecasts.board(limit=5)
    assert unnamed(board) == []
    assert [b.key for b in board.baselines] == ["fair", "market"]
    assert len(board.rows) <= min(5, board.total)
    assert len(board.to_dataframe()) == len(board.rows)

    days = ho.forecasts.records()
    assert unnamed(days) == []
    assert len(days) == board.records
    if days:
        lines = ho.forecasts.day(days[0].day)
        assert unnamed(lines) == []
        assert len(lines) == days[0].count
        assert len(lines.to_dataframe()) == len(lines)

    with pytest.raises(NotFoundError):
        ho.forecasts.day(date(2020, 1, 1))
