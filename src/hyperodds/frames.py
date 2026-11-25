"""pandas tables built from API results.

Every result with a ``to_dataframe()`` method calls one of these. They need pandas, which comes
with ``pip install "hyperodds[pandas]"`` (Google Colab has it already). Times are UTC
``datetime64`` columns, prices are decimals from 0 to 1, and ids that can be missing use pandas'
nullable ``Int64``.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

    from .models import (
        Bar,
        Book,
        Cluster,
        Comparison,
        ForecastBoard,
        HistoryPoint,
        Market,
        MarketDetail,
        Match,
        Question,
        RecordDay,
        RecordLine,
        Sheet,
        Trade,
        VenueHistory,
    )


def _pandas() -> Any:
    try:
        import pandas
    except ImportError as e:
        raise ImportError(
            'to_dataframe() needs pandas. Install it with: pip install "hyperodds[pandas]"'
        ) from e
    return pandas


def _frame(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[str],
    *,
    index: str | None = None,
    times: Sequence[str] = (),
    floats: Sequence[str] = (),
    ints: Sequence[str] = (),
    bools: Sequence[str] = (),
) -> pd.DataFrame:
    pandas = _pandas()
    df: pd.DataFrame = pandas.DataFrame(list(rows), columns=list(columns))
    for col in times:
        df[col] = pandas.to_datetime(df[col], utc=True)
    for col in floats:
        df[col] = df[col].astype("float64")
    for col in ints:
        df[col] = df[col].astype("Int64")
    for col in bools:
        df[col] = df[col].astype("boolean")
    return df.set_index(index) if index else df


_MARKET_COLUMNS = (
    "outcome", "title", "label", "category", "subcategory", "deployer", "deployer_name",
    "template", "kind", "role", "question_id", "question_title", "side0", "side1", "status",
    "settled_side", "starts", "settles_at", "settles_by", "event_time", "deadline", "listed_at",
    "bid", "ask", "mid", "spread", "last_px", "change_24h", "volume_24h", "trades_24h",
    "depth_bid_usd", "depth_ask_usd", "fair", "fair_lo", "fair_hi", "fair_method",
    "fair_confidence", "fair_single_source", "edge_side", "edge_pts", "cluster_id", "cluster_size",
    "match_grade", "settled_by", "settlement_risk", "url",
)  # fmt: skip

_MARKET_TIMES = ("starts", "settles_at", "settles_by", "event_time", "deadline", "listed_at")

_MARKET_FLOATS = (
    "bid", "ask", "mid", "spread", "last_px", "change_24h", "volume_24h", "depth_bid_usd",
    "depth_ask_usd", "fair", "fair_lo", "fair_hi", "fair_confidence", "edge_pts",
)  # fmt: skip


def _market_row(m: Market) -> dict[str, Any]:
    fair, best = m.fair, m.edge.best if m.edge else None
    return {
        "outcome": m.outcome,
        "title": m.title,
        "label": m.label,
        "category": m.category,
        "subcategory": m.subcategory,
        "deployer": m.deployer,
        "deployer_name": m.deployer_name,
        "template": m.template,
        "kind": m.kind,
        "role": m.role,
        "question_id": m.question_id,
        "question_title": m.question_title,
        "side0": m.side_labels[0],
        "side1": m.side_labels[1],
        "status": m.status,
        "settled_side": m.settled_side,
        "starts": m.starts,
        "settles_at": m.settles_at,
        "settles_by": m.settles_by,
        "event_time": m.event_time,
        "deadline": m.deadline,
        "listed_at": m.listed_at,
        "bid": m.bid,
        "ask": m.ask,
        "mid": m.mid,
        "spread": m.spread,
        "last_px": m.last_px,
        "change_24h": m.change_24h,
        "volume_24h": m.volume_24h,
        "trades_24h": m.trades_24h,
        "depth_bid_usd": m.depth_usd.bid,
        "depth_ask_usd": m.depth_usd.ask,
        "fair": fair.p if fair else None,
        "fair_lo": fair.lo if fair else None,
        "fair_hi": fair.hi if fair else None,
        "fair_method": fair.method if fair else None,
        "fair_confidence": fair.confidence if fair else None,
        "fair_single_source": fair.single_source if fair else None,
        "edge_side": best.side if best else None,
        "edge_pts": best.pts if best else None,
        "cluster_id": m.cluster_id,
        "cluster_size": m.cluster_size,
        "match_grade": m.match_grade,
        "settled_by": m.settlement_risk.settled_by,
        "settlement_risk": m.settlement_risk.level,
        "url": m.url,
    }


def markets(items: Iterable[Market]) -> pd.DataFrame:
    """One row per market, indexed by outcome id.

    ``fair`` is the fair probability of side 0 and ``edge_pts`` the best buy edge after fees, in
    points, on the side ``edge_side``. ``starts``, ``settles_at`` and ``settles_by`` are the
    console's times (see :class:`~hyperodds.Market`).
    """
    return _frame(
        [_market_row(m) for m in items],
        _MARKET_COLUMNS,
        index="outcome",
        times=_MARKET_TIMES,
        floats=_MARKET_FLOATS,
        ints=("question_id", "settled_side", "edge_side"),
        bools=("fair_single_source",),
    )


def book(b: Book) -> pd.DataFrame:
    """One row per level, bids then asks, best first, with ``cum_sz``: shares up to that price.

    ``book_side`` is the market side the prices are for (see :class:`~hyperodds.Book`).
    """
    rows: list[dict[str, Any]] = []
    for side, levels in (("bid", b.bids), ("ask", b.asks)):
        total = 0.0
        for lv in levels:
            total += lv.sz
            rows.append(
                {
                    "book_side": b.side,
                    "side": side,
                    "px": lv.px,
                    "sz": lv.sz,
                    "cum_sz": total,
                    "n": lv.n,
                    "source": lv.source,
                }
            )
    df = _frame(
        rows,
        ("book_side", "side", "px", "sz", "cum_sz", "n", "source"),
        floats=("px", "sz", "cum_sz"),
        ints=("book_side", "n"),
    )
    if df["source"].isna().all():
        df = df.drop(columns="source")
    return df


def trades(items: Iterable[Trade]) -> pd.DataFrame:
    """One row per trade, newest first, indexed by trade id."""
    rows = [
        {
            "tid": t.tid,
            "at": t.at,
            "px": t.px,
            "sz": t.sz,
            "side": t.side,
            "taker_side": t.taker_side,
        }
        for t in items
    ]
    return _frame(
        rows,
        ("tid", "at", "px", "sz", "side", "taker_side"),
        index="tid",
        times=("at",),
        floats=("px", "sz"),
    )


_MATCH_COLUMNS = (
    "venue", "id", "title", "grade", "flipped", "auto", "trading", "bid", "ask", "mid", "book_ts",
    "url",
)  # fmt: skip


def _match_row(m: Match) -> dict[str, Any]:
    return {
        "venue": m.venue,
        "id": m.id,
        "title": m.title,
        "grade": m.grade,
        "flipped": m.flipped,
        "auto": m.auto,
        "trading": m.trading,
        "bid": m.bid,
        "ask": m.ask,
        "mid": m.mid,
        "book_ts": m.book_ts,
        "url": m.url,
    }


def matches(items: Iterable[Match]) -> pd.DataFrame:
    """One row per matched Polymarket (``pm``) or Kalshi (``kx``) market, prices in HIP-4 terms."""
    return _frame(
        [_match_row(m) for m in items],
        _MATCH_COLUMNS,
        times=("book_ts",),
        floats=("bid", "ask", "mid"),
    )


_VENUE_MATCH_COLUMNS = (
    "outcome", "title", "category", "deployer", "venue", "venue_id", "venue_title", "grade",
    "differences", "flipped", "hl_bid", "hl_ask", "hl_mid", "hl_spread", "venue_bid", "venue_ask",
    "venue_mid", "venue_spread", "gap_pts", "hl_url", "venue_url",
)  # fmt: skip

_VENUE_MATCH_FLOATS = (
    "hl_bid", "hl_ask", "hl_mid", "hl_spread", "venue_bid", "venue_ask", "venue_mid",
    "venue_spread", "gap_pts",
)  # fmt: skip


def _spread(bid: float | None, ask: float | None) -> float | None:
    return None if bid is None or ask is None else ask - bid


def venue_matches(details: Iterable[MarketDetail]) -> pd.DataFrame:
    """HIP-4 next to every Polymarket and Kalshi market matched to it, one row per match.

    ``hl_*`` columns are the HIP-4 book, ``venue_*`` the matched market's, both as the price of the
    HIP-4 market's side 0. ``gap_pts`` is the HIP-4 mid less the venue mid, in points.

    Read it with ``grade``: SAME markets settle on the same terms, CLOSE ones differ in a detail
    (``differences`` names the rule fields, such as ``cutoff, source``), DIFFERENT ones ask
    another question. Check the spreads too: a book whose only quotes are a bid at 0.01 and an
    ask at 0.99 has a mid of 0.50 whatever the market thinks.
    """
    rows: list[dict[str, Any]] = []
    for d in details:
        s = d.summary
        for m in d.matches:
            gap = None
            if s.mid is not None and m.mid is not None:
                gap = (s.mid - m.mid) * 100
            rows.append(
                {
                    "outcome": s.outcome,
                    "title": s.title,
                    "category": s.category,
                    "deployer": s.deployer,
                    "venue": m.venue,
                    "venue_id": m.id,
                    "venue_title": m.title,
                    "grade": m.grade,
                    "differences": ", ".join(f.field for f in m.diffs if f.severity != "info"),
                    "flipped": m.flipped,
                    "hl_bid": s.bid,
                    "hl_ask": s.ask,
                    "hl_mid": s.mid,
                    "hl_spread": _spread(s.bid, s.ask),
                    "venue_bid": m.bid,
                    "venue_ask": m.ask,
                    "venue_mid": m.mid,
                    "venue_spread": _spread(m.bid, m.ask),
                    "gap_pts": gap,
                    "hl_url": s.url,
                    "venue_url": m.url,
                }
            )
    return _frame(
        rows,
        _VENUE_MATCH_COLUMNS,
        floats=_VENUE_MATCH_FLOATS,
    )


_QUESTION_COLUMNS = (
    "question", "title", "category", "deployer", "template", "kind", "starts", "settles_at",
    "settles_by", "event_time", "deadline", "outcomes", "complete", "sum_asks", "sum_bids",
    "buy_gap", "sell_gap", "sum_fair", "url",
)  # fmt: skip


def questions(items: Iterable[Question]) -> pd.DataFrame:
    """One row per question, indexed by question id, with its named outcomes' set sums."""
    rows = [
        {
            "question": q.question,
            "title": q.title,
            "category": q.category,
            "deployer": q.deployer,
            "template": q.template,
            "kind": q.kind,
            "starts": q.starts,
            "settles_at": q.settles_at,
            "settles_by": q.settles_by,
            "event_time": q.event_time,
            "deadline": q.deadline,
            "outcomes": len(q.outcomes),
            "complete": q.set.complete,
            "sum_asks": q.set.sum_asks,
            "sum_bids": q.set.sum_bids,
            "buy_gap": q.set.buy_gap,
            "sell_gap": q.set.sell_gap,
            "sum_fair": q.set.sum_fair,
            "url": q.url,
        }
        for q in items
    ]
    return _frame(
        rows,
        _QUESTION_COLUMNS,
        index="question",
        times=("starts", "settles_at", "settles_by", "event_time", "deadline"),
        floats=("sum_asks", "sum_bids", "buy_gap", "sell_gap", "sum_fair"),
    )


def cluster(c: Cluster) -> pd.DataFrame:
    """The cluster's markets as in :func:`markets`, with each one's grade against the anchor."""
    df = markets(member.summary for member in c.members)
    df.insert(0, "grade", [member.grade for member in c.members])
    df.insert(1, "flipped", [member.flipped for member in c.members])
    return df


def clusters(items: Iterable[Cluster]) -> pd.DataFrame:
    """Every member of every cluster, as in :func:`cluster`, with its cluster's title first.

    ``cluster_id`` names the cluster; ``grade`` and ``flipped`` are against its anchor.
    """
    pairs = [(c, member) for c in items for member in c.members]
    df = markets(member.summary for _, member in pairs)
    df.insert(0, "cluster_title", [c.title for c, _ in pairs])
    df.insert(1, "grade", [member.grade for _, member in pairs])
    df.insert(2, "flipped", [member.flipped for _, member in pairs])
    return df


def comparison(c: Comparison) -> pd.DataFrame:
    """One row per venue for the same order, HIP-4 (``hl``) first.

    ``all_in_px`` is the price per share after trading fees, ``fees`` their USD total for the
    whole order, and ``filled`` the shares the book can fill. ``held_all_in_px`` is the price per
    dollar of payout if the side wins, the column to compare: Hyperliquid takes its settlement
    fee from the payout, so the HIP-4 row is a little above its ``all_in_px``, while Polymarket
    and Kalshi charge only on the trade.
    """
    rows: list[dict[str, Any]] = [
        {
            "venue": "hl",
            "title": None,
            "grade": None,
            "avg_px": c.hl.avg_px,
            "all_in_px": c.hl.all_in_px,
            "held_all_in_px": c.hl.held_all_in_px,
            "fees": c.hl.fees,
            "filled": c.hl.filled,
            "blocked": c.hl.blocked,
            "url": None,
        }
    ]
    for v in c.venues:
        q = v.quote
        rows.append(
            {
                "venue": v.venue,
                "title": v.title,
                "grade": v.grade,
                "avg_px": q.avg_px if q else None,
                "all_in_px": q.all_in_px if q else None,
                "held_all_in_px": q.all_in_px if q else None,
                "fees": q.fee if q else None,
                "filled": q.filled if q else None,
                "blocked": v.blocked,
                "url": v.url,
            }
        )
    return _frame(
        rows,
        (
            "venue",
            "title",
            "grade",
            "avg_px",
            "all_in_px",
            "held_all_in_px",
            "fees",
            "filled",
            "blocked",
            "url",
        ),
        floats=("avg_px", "all_in_px", "held_all_in_px", "fees", "filled"),
    )


def history(points: Iterable[HistoryPoint]) -> pd.DataFrame:
    """One row per moment, indexed by time: the last trade and the fair line then in force."""
    rows = [
        {
            "at": h.at,
            "last_px": h.last_px,
            "fair": h.fair,
            "fair_lo": h.lo,
            "fair_hi": h.hi,
            "fair_confidence": h.confidence,
            "fair_single_source": h.single_source,
        }
        for h in points
    ]
    return _frame(
        rows,
        (
            "at",
            "last_px",
            "fair",
            "fair_lo",
            "fair_hi",
            "fair_confidence",
            "fair_single_source",
        ),
        index="at",
        times=("at",),
        floats=("last_px", "fair", "fair_lo", "fair_hi", "fair_confidence"),
        bools=("fair_single_source",),
    )


def venue_history(h: VenueHistory) -> pd.DataFrame:
    """One row per price, each venue's oldest first. ``p`` is the price of the market's side 0.

    Pivot it for one column per venue: ``df.pivot(index="at", columns="venue", values="p")``.
    The two venues print at different moments, so fill forward before comparing them.
    """
    rows = [
        {
            "at": point.at,
            "venue": line.venue,
            "p": point.p,
            "grade": line.grade,
            "flipped": line.flipped,
            "venue_id": line.id,
            "venue_title": line.title,
        }
        for line in h.lines
        for point in line.prints
    ]
    return _frame(
        rows,
        ("at", "venue", "p", "grade", "flipped", "venue_id", "venue_title"),
        times=("at",),
        floats=("p",),
        bools=("flipped",),
    )


def bars(items: Iterable[Bar]) -> pd.DataFrame:
    """One row per bar, indexed by its opening time. Prices from 0 to 1, volume in contracts."""
    rows = [
        {
            "at": b.at,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in items
    ]
    return _frame(
        rows,
        ("at", "open", "high", "low", "close", "volume"),
        index="at",
        times=("at",),
        floats=("open", "high", "low", "close", "volume"),
    )


_SHEET_TIMES = ("starts_utc", "settles_by_utc")


def sheet(text: Sheet) -> pd.DataFrame:
    """A sheet's rows under its own column names, or numbered columns for one read without them."""
    pandas = _pandas()
    # An empty sheet is one quoted empty field, which spreadsheets read as an empty cell.
    if text.strip() in ("", '""'):
        return pandas.DataFrame()  # type: ignore[no-any-return]
    df: pd.DataFrame = pandas.read_csv(io.StringIO(text), header=0 if text.header else None)
    for col in _SHEET_TIMES:
        if col in df.columns:
            df[col] = pandas.to_datetime(df[col], utc=True)
    return df


_STATS = ("n", "brier", "n_fair", "skill_fair", "skill_fair_low", "n_market", "skill_market")


def board(b: ForecastBoard) -> pd.DataFrame:
    """One row per forecaster on the page, indexed by rank."""
    rows = [
        {"rank": r.rank, "forecaster": r.forecaster, "early": r.early}
        | {name: getattr(r, name) for name in _STATS}
        for r in b.rows
    ]
    return _frame(
        rows,
        ("rank", "forecaster", "early", *_STATS),
        index="rank",
        floats=("brier", "skill_fair", "skill_fair_low", "skill_market"),
        ints=("n", "n_fair", "n_market"),
        bools=("early",),
    )


def record_days(items: Iterable[RecordDay]) -> pd.DataFrame:
    """One row per closed day of the forecast record, indexed by day."""
    rows = [
        {
            "day": d.day,
            "count": d.count,
            "root": d.root,
            "sha256": d.sha256,
            "bytes": d.bytes,
            "closed_at": d.closed_at,
            "url": d.url,
        }
        for d in items
    ]
    return _frame(
        rows,
        ("day", "count", "root", "sha256", "bytes", "closed_at", "url"),
        index="day",
        times=("closed_at",),
        ints=("count", "bytes"),
    )


def record_lines(items: Iterable[RecordLine]) -> pd.DataFrame:
    """One row per recorded forecast, indexed by id.

    ``probability``, ``fair`` and ``mid`` are the record's basis points as decimals from 0 to 1.
    """

    def unit(bps: int | None) -> float | None:
        return None if bps is None else bps / 10_000

    rows = [
        {
            "id": f.id,
            "forecaster": f.forecaster,
            "outcome": f.outcome,
            "probability": unit(f.probability_bps),
            "fair": unit(f.fair_bps),
            "mid": unit(f.mid_bps),
            "accepted_at": f.accepted_at,
            "cutoff": f.cutoff,
            "book_time": f.book_time,
            "fair_note": f.fair_note,
            "signature": f.signature,
        }
        for f in items
    ]
    return _frame(
        rows,
        (
            "id",
            "forecaster",
            "outcome",
            "probability",
            "fair",
            "mid",
            "accepted_at",
            "cutoff",
            "book_time",
            "fair_note",
            "signature",
        ),
        index="id",
        times=("accepted_at", "cutoff", "book_time"),
        floats=("probability", "fair", "mid"),
    )
