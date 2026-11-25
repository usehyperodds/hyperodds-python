import json
import sys
from datetime import date

import pandas as pd
import pytest
from pydantic import TypeAdapter

from hyperodds import (
    Book,
    Cluster,
    ClusterList,
    Comparison,
    ForecastBoard,
    History,
    MarketDetail,
    MarketDetails,
    MarketList,
    Question,
    QuestionList,
    RecordDayList,
    RecordLineList,
    Sheet,
    TradeList,
    VenueHistory,
)
from hyperodds.frames import venue_matches

from .conftest import MakeClient, data, load, text


def markets() -> MarketList:
    return TypeAdapter(MarketList).validate_python(data("markets.list.json"))


def details() -> MarketDetails:
    found = MarketDetails()
    for kind in ("matched", "options", "cluster", "named"):
        detail = MarketDetail.model_validate(data(f"markets.get.{kind}.json"))
        found[detail.outcome] = detail
    return found


def test_markets_frame_has_one_row_per_market_by_outcome() -> None:
    ms = markets()
    df = ms.to_dataframe()
    assert df.index.name == "outcome"
    assert list(df.index) == [m.outcome for m in ms]
    assert df.index.is_unique


def test_markets_frame_types() -> None:
    df = markets().to_dataframe()
    for col in ("starts", "settles_at", "settles_by", "event_time", "deadline", "listed_at"):
        assert isinstance(df[col].dtype, pd.DatetimeTZDtype)
        assert str(df[col].dt.tz) == "UTC"
    for col in ("bid", "ask", "mid", "fair", "edge_pts", "volume_24h", "depth_bid_usd"):
        assert df[col].dtype == "float64", col
    for col in ("question_id", "settled_side", "edge_side"):
        assert df[col].dtype == "Int64", col
    assert df["fair_single_source"].dtype == "boolean"


def test_markets_frame_values_match_the_api() -> None:
    raw = {m["outcome"]: m for m in data("markets.list.json")}
    df = markets().to_dataframe()
    for outcome, row in df.iterrows():
        m = raw[outcome]
        assert row["title"] == m["title"]
        assert (row["side0"], row["side1"]) == tuple(m["sideLabels"])
        assert row["depth_bid_usd"] == m["depthUsd"]["bid"]
        if m["fair"]:
            assert row["fair"] == m["fair"]["p"]
            assert row["fair_method"] == m["fair"]["method"]
        else:
            assert pd.isna(row["fair"])
        best = m["edge"]["best"] if m["edge"] else None
        if best:
            assert row["edge_pts"] == best["pts"]
            assert row["edge_side"] == best["side"]
        else:
            assert pd.isna(row["edge_pts"])
        if m["eventTime"] is None:
            assert pd.isna(row["event_time"])
        else:
            assert row["event_time"] == pd.Timestamp(m["eventTime"], unit="ms", tz="UTC")


def test_markets_frame_times_follow_the_console() -> None:
    ms = markets()
    df = ms.to_dataframe()
    for m in ms:
        for col in ("starts", "settles_at", "settles_by"):
            value = getattr(m, col)
            cell = df.loc[m.outcome, col]
            assert pd.isna(cell) if value is None else cell == pd.Timestamp(value)
    assert (df.kind == [m.kind for m in ms]).all()


def test_an_empty_list_keeps_its_columns() -> None:
    df = MarketList().to_dataframe()
    assert len(df) == 0
    assert "edge_pts" in df.columns
    assert df.index.name == "outcome"


def test_book_frame_adds_up_shares_from_the_best_price() -> None:
    book = Book.model_validate(data("markets.book.json"))
    df = book.to_dataframe()
    bids, asks = df[df.side == "bid"], df[df.side == "ask"]
    assert list(bids.px) == [lv.px for lv in book.bids]
    assert bids.px.is_monotonic_decreasing
    assert asks.px.is_monotonic_increasing
    assert bids.cum_sz.iloc[-1] == sum(lv.sz for lv in book.bids)
    assert "source" not in df.columns
    assert (df.book_side == 0).all()


def test_a_merged_book_says_where_each_level_comes_from() -> None:
    cluster = Cluster.model_validate(data("clusters.get.json"))
    assert cluster.merged_book is not None
    df = cluster.merged_book.to_dataframe()
    assert df.source.str.startswith("hl:").all()


def test_trades_frame() -> None:
    trades = TypeAdapter(TradeList).validate_python(data("markets.trades.json"))
    df = trades.to_dataframe()
    assert df.index.name == "tid"
    assert list(df.columns) == ["at", "px", "sz", "side", "taker_side"]
    assert str(df["at"].dt.tz) == "UTC"


def test_venue_matches_put_hip4_beside_each_venue() -> None:
    found = details()
    df = found.matches_dataframe()
    assert len(df) == sum(len(d.matches) for d in found.values())
    for _, row in df.iterrows():
        detail = found[row["outcome"]]
        match = next(m for m in detail.matches if m.id == row["venue_id"])
        assert row["hl_mid"] == detail.summary.mid
        if match.bid is None:
            assert pd.isna(row["venue_bid"])
        else:
            assert row["venue_bid"] == match.bid
        assert row["differences"] == ", ".join(f.field for f in match.diffs if f.severity != "info")
        if match.bid is not None and match.ask is not None:
            assert row["venue_spread"] == pytest.approx(match.ask - match.bid)
        if match.mid is None or detail.summary.mid is None:
            assert pd.isna(row["gap_pts"])
        else:
            assert row["gap_pts"] == pytest.approx((detail.summary.mid - match.mid) * 100)
    assert set(df.venue) <= {"pm", "kx"}


def test_venue_matches_of_markets_without_matches_is_empty() -> None:
    unmatched = {**data("markets.get.named.json"), "matches": []}
    df = venue_matches([MarketDetail.model_validate(unmatched)])
    assert df.empty
    assert "gap_pts" in df.columns


def test_questions_frame_has_the_set_sums() -> None:
    questions = TypeAdapter(QuestionList).validate_python(data("questions.list.json"))
    df = questions.to_dataframe()
    assert df.index.name == "question"
    first = questions[0]
    assert df.loc[first.question, "complete"] == first.set.complete
    assert df.loc[first.question, "outcomes"] == len(first.outcomes)
    assert df.loc[first.question, "kind"] == first.kind
    assert str(df["settles_by"].dt.tz) == "UTC"


def test_a_question_frame_lists_its_outcomes_and_fallback() -> None:
    question = Question.model_validate(data("questions.get.json"))
    df = question.to_dataframe()
    assert len(df) == len(question.outcomes) + (question.fallback is not None)
    assert (df.question_id == question.question).all()


def test_cluster_frame_grades_each_member() -> None:
    cluster = Cluster.model_validate(data("clusters.get.json"))
    df = cluster.to_dataframe()
    assert list(df.grade) == [m.grade for m in cluster.members]
    assert list(df.columns[:2]) == ["grade", "flipped"]


def test_cluster_list_frame_has_every_member_of_every_cluster() -> None:
    clusters = TypeAdapter(ClusterList).validate_python(data("clusters.list.json"))
    df = clusters.to_dataframe()
    members = [(c, m) for c in clusters for m in c.members]
    assert list(df.index) == [m.summary.outcome for _, m in members]
    assert list(df.columns[:3]) == ["cluster_title", "grade", "flipped"]
    assert list(df.cluster_title) == [c.title for c, _ in members]
    assert list(df.cluster_id) == [c.id for c, _ in members]
    assert ClusterList().to_dataframe().empty


def test_comparison_frame_puts_hip4_first() -> None:
    comparison = Comparison.model_validate(data("quotes.compare.json"))
    df = comparison.to_dataframe()
    assert df.venue.iloc[0] == "hl"
    assert len(df) == 1 + len(comparison.venues)
    assert df.all_in_px.iloc[0] == comparison.hl.all_in_px


def test_comparison_frame_compares_on_the_price_held_to_settlement() -> None:
    comparison = Comparison.model_validate(data("quotes.compare.json"))
    df = comparison.to_dataframe()
    assert df.held_all_in_px.iloc[0] == comparison.hl.held_all_in_px
    assert df.held_all_in_px.iloc[0] > df.all_in_px.iloc[0]
    venues = df.iloc[1:].dropna(subset=["all_in_px"])
    assert (venues.held_all_in_px == venues.all_in_px).all()


def test_details_frame_is_the_summaries() -> None:
    found = details()
    assert list(found.to_dataframe().index) == list(found)


def test_without_pandas_the_error_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pandas", None)
    with pytest.raises(ImportError, match=r'pip install "hyperodds\[pandas\]"'):
        markets().to_dataframe()


def test_matches_frame_says_whether_each_venue_market_trades() -> None:
    detail = MarketDetail.model_validate(data("markets.get.matched.json"))
    df = detail.matches.to_dataframe()
    assert list(df.trading) == [m.trading for m in detail.matches]


def test_history_frame_is_indexed_by_time() -> None:
    points = TypeAdapter(History).validate_python(data("markets.history.json"))
    df = points.to_dataframe()
    assert df.index.name == "at"
    assert str(df.index.dtype).endswith(", UTC]")
    assert df.index.is_monotonic_increasing
    assert list(df.columns) == [
        "last_px",
        "fair",
        "fair_lo",
        "fair_hi",
        "fair_confidence",
        "fair_single_source",
    ]
    assert df.fair_single_source.dtype == "boolean"
    assert df.last_px.iloc[-1] == points[-1].last_px


def test_a_point_without_a_line_is_missing_in_the_frame() -> None:
    raw = [{**data("markets.history.json")[0], "fair": None, "lo": None, "hi": None,
            "singleSource": None, "confidence": None}]  # fmt: skip
    df = TypeAdapter(History).validate_python(raw).to_dataframe()
    assert (
        df[["fair", "fair_lo", "fair_hi", "fair_confidence", "fair_single_source"]]
        .isna()
        .all(axis=None)
    )


def test_venue_history_frame_has_one_row_per_price() -> None:
    venues = VenueHistory.model_validate(data("markets.venueHistory.json"))
    df = venues.to_dataframe()
    assert len(df) == sum(len(line.prints) for line in venues.lines)
    assert str(df["at"].dt.tz) == "UTC"
    assert set(df.venue) == {line.venue for line in venues.lines if line.prints}
    wide = df.pivot(index="at", columns="venue", values="p")
    assert set(wide.columns) == set(df.venue)
    assert df.p.between(0, 1).all()


def test_bars_frame(make_client: MakeClient) -> None:
    bars = make_client().charts.bars(1473, "1d", limit=12)
    df = bars.to_dataframe()
    assert df.index.name == "at"
    assert str(df.index.dtype).endswith(", UTC]")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == len(bars)
    assert (df.high >= df.low).all()
    assert df.close.iloc[-1] == bars[-1].close


def test_sheet_frame_reads_the_columns_and_times() -> None:
    df = Sheet(text("rest/sheets.markets.csv")).to_dataframe()
    assert list(df.columns[:3]) == ["outcome", "title", "label"]
    assert len(df) == 3
    assert str(df.settles_by_utc.dt.tz) == "UTC"
    assert df.url.str.startswith("https://usehyperodds.com/m/").all()


def test_a_sheet_without_a_header_has_numbered_columns() -> None:
    df = Sheet("1473,0.727735\n", header=False).to_dataframe()
    assert df.values.tolist() == [[1473, 0.727735]]
    assert [int(c) for c in df.columns] == [0, 1]


@pytest.mark.parametrize("empty", ["", '""', '""\n'])
def test_an_empty_sheet_is_an_empty_frame(empty: str) -> None:
    assert Sheet(empty, header=False).to_dataframe().empty


def test_board_frame_is_indexed_by_rank() -> None:
    board = ForecastBoard.model_validate(load("rest/forecasts.board.spec.json"))
    df = board.to_dataframe()
    assert list(df.index) == [1, 2]
    assert df.index.name == "rank"
    assert df.early.dtype == "boolean"
    assert df.n.dtype == "Int64"
    assert df.loc[1, "skill_fair_low"] == 0.001874


def test_an_empty_board_keeps_its_columns() -> None:
    df = ForecastBoard.model_validate(load("rest/forecasts.board.json")).to_dataframe()
    assert "skill_fair" in df.columns
    assert len(df) == len(load("rest/forecasts.board.json")["rows"])


def test_record_days_frame() -> None:
    days = TypeAdapter(RecordDayList).validate_python(
        load("rest/forecasts.records.spec.json")["days"]
    )
    df = days.to_dataframe()
    assert list(df.index) == [date(2025, 11, 22), date(2025, 11, 21)]
    assert df["count"].sum() == 7
    assert str(df.closed_at.dt.tz) == "UTC"


def test_record_lines_frame_has_probabilities_from_0_to_1() -> None:
    raw = [json.loads(line) for line in text("rest/forecasts.day.spec.jsonl").splitlines()]
    df = TypeAdapter(RecordLineList).validate_python(raw).to_dataframe()
    assert list(df.index) == [101, 102]
    assert df.loc[101, "probability"] == 0.62
    assert df.loc[101, "fair"] == 0.581
    assert pd.isna(df.loc[102, "mid"])
    assert pd.isna(df.loc[102, "book_time"])
    assert str(df.cutoff.dt.tz) == "UTC"
