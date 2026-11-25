"""Typed results of the Hyperodds API.

Field names are the API's, in snake case (``volume24h`` is ``volume_24h``). Times are timezone-aware
UTC datetimes. Prices and probabilities are decimals from 0 to 1 for side 0 of a market (usually
Yes); edges are in percentage points. Fields the API adds later are kept in ``model_extra``
under the API's own camelCase names, such as ``newField``.
"""

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, GetCoreSchemaHandler
from pydantic.alias_generators import to_camel
from pydantic_core import CoreSchema, core_schema

if TYPE_CHECKING:
    import pandas as pd

CONSOLE_URL = "https://usehyperodds.com"


def _from_ms(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return value


Timestamp = Annotated[datetime, BeforeValidator(_from_ms)]
"""Milliseconds since the Unix epoch on the wire, a UTC datetime here."""


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, extra="allow", frozen=True)


class _Records:
    """A list that pydantic validates item by item and pandas can take whole."""

    _item: ClassVar[Any]

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            handler.generate_schema(list[cls._item]),  # type: ignore[name-defined]
        )


_PRICE_KINDS = frozenset({"price_binary", "price_bucket", "price_touch"})


class Timing(Model):
    """A market's times as the console labels them: ``settles`` or ``by``, never both."""

    starts: Timestamp | None = None
    settles: Timestamp | None = None
    by: Timestamp | None = None


class _Timed:
    """When a market starts and settles, as the console reads it."""

    if TYPE_CHECKING:
        kind: str
        event_time: datetime | None
        deadline: datetime | None

    def _timing(self) -> Timing:
        # Markets come with the API's own reading. Questions do not, so the same rule runs here.
        sent: Timing | None = getattr(self, "timing", None)
        if sent is not None:
            return sent
        if self.kind not in _PRICE_KINDS:
            return Timing(starts=self.event_time, by=self.deadline)
        if self.event_time is not None and self.deadline in (None, self.event_time):
            return Timing(settles=self.event_time)
        return Timing(by=self.deadline or self.event_time)

    @property
    def starts(self) -> datetime | None:
        """A game's start or, for ``policy_rate``, the decision's time. None for price markets.

        The console labels it "Starts" or "Decision". The market settles later, by ``settles_by``.
        """
        return self._timing().starts

    @property
    def settles_at(self) -> datetime | None:
        """When a price market settles at a set price time ("Settles at"). None otherwise.

        A touch market (``price_touch``) has no single price time: it has ``settles_by``.
        """
        return self._timing().settles

    @property
    def settles_by(self) -> datetime | None:
        """The latest the market settles, its deadline ("Settles by"). None with ``settles_at``."""
        return self._timing().by


class DepthUsd(Model):
    bid: float
    ask: float


class FairSource(Model):
    """One input to a fair line. ``venue`` and ``label`` name it for people, where it has a name."""

    name: str
    p: float
    weight: float
    age_ms: float
    venue: str | None = None
    label: str | None = None


class FairLine(Model):
    """The probability that side 0 wins, its band, and where it comes from.

    ``method`` is ``options``, ``realized-vol``, ``blend`` or ``consensus``. ``sources`` is only
    sent by ``markets.get``. ``single_source`` is True for a line on one input: one venue's quote
    (``consensus``) or realized volatility alone. An options surface counts as more than one
    source, so ``options`` and ``blend`` lines are False even with one options venue.
    """

    p: float
    lo: float | None = None
    hi: float | None = None
    method: str
    sources: list[FairSource] | None = None
    single_source: bool
    confidence: float
    flags: list[str] = []
    ts: Timestamp | None = None


class BestEdge(Model):
    side: int
    action: str
    pts: float


class Edge(Model):
    """Edge in points after every fee for each way to trade ``ref_usd`` dollars.

    None where that size does not fill.
    """

    ref_usd: float
    buy0: float | None
    buy1: float | None
    sell0: float | None
    sell1: float | None
    best: BestEdge | None


class PastDelays(Model):
    count: int
    median_ms: float | None
    max_ms: float | None


class SettlementRisk(Model):
    settled_by: str
    deployer: str | None = None
    stake_hype: float | None = None
    unstaking_hype: float | None = None
    past_delays: PastDelays | None = None
    level: str
    note: str | None = None


class Market(Model, _Timed):
    """One HIP-4 market: a scanner row.

    ``kind`` is the event kind, as in the market's rules: ``contest_winner``, ``policy_rate``,
    ``price_binary`` and so on. ``role`` is ``standalone``, ``named`` or ``fallback``. ``status``
    is ``open``, ``live``, ``awaiting``, ``settled`` or ``removed``. ``match_grade`` is the best
    grade among matched Polymarket and Kalshi markets: ``SAME``, ``CLOSE``, ``DIFFERENT`` or None.

    ``starts``, ``settles_at`` and ``settles_by`` time the market as the console does, from the
    API's ``timing``; ``event_time`` and ``deadline`` are the raw times behind them.
    ``volume_24h`` counts contracts.
    """

    outcome: int
    question_id: int | None
    role: str
    deployer: str
    deployer_name: str
    template: str
    kind: str
    title: str
    label: str
    question_title: str | None
    side_labels: tuple[str, str]
    category: str
    subcategory: str | None
    event_time: Timestamp | None
    deadline: Timestamp | None
    listed_at: Timestamp | None
    timing: Timing | None = None
    status: str
    settled_side: int | None
    bid: float | None
    ask: float | None
    mid: float | None
    spread: float | None
    depth_usd: DepthUsd
    volume_24h: float = Field(alias="volume24h")
    trades_24h: int = Field(alias="trades24h")
    last_px: float | None
    change_24h: float | None = Field(alias="change24h")
    fair: FairLine | None
    edge: Edge | None
    cluster_id: str | None
    cluster_size: int
    match_grade: str | None
    settlement_risk: SettlementRisk

    @property
    def url(self) -> str:
        """The market's page in the Hyperodds console."""
        return f"{CONSOLE_URL}/m/{self.outcome}"


class MarketList(_Records, list[Market]):
    _item = Market

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per market, indexed by outcome id."""
        from . import frames

        return frames.markets(self)


class Level(Model):
    """A price level: price, shares, and the number of orders when the venue reports it."""

    px: float
    sz: float
    n: int | None = None
    source: str | None = None


class Book(Model):
    """An order book. Bids run best (highest) first, asks best (lowest) first.

    ``side`` is the market side whose prices these are: 1 for ``markets.book(outcome, side=1)``,
    else 0. Match books and merged books are always in side-0 terms. The API does not send it.
    """

    bids: list[Level]
    asks: list[Level]
    ts: Timestamp | None = None
    side: int = Field(default=0, exclude=True)

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per level with the shares available up to that price."""
        from . import frames

        return frames.book(self)


class Trade(Model):
    """A trade: ``px`` is the side-0 price, ``side`` the coin that printed.

    ``taker_side`` is ``B`` when the taker bought and ``A`` when it sold.
    """

    tid: int
    px: float
    sz: float
    notional: float
    side: int
    taker_side: str
    at: Timestamp


class TradeList(_Records, list[Trade]):
    _item = Trade

    def to_dataframe(self) -> "pd.DataFrame":
        from . import frames

        return frames.trades(self)


class Diff(Model):
    """A difference between two markets' rules. ``severity`` is info, close or different."""

    field: str
    a: Any = None
    b: Any = None
    severity: str
    note: str | None = None


class Rules(Model):
    """A market's resolution terms, parsed."""

    kind: str | None = None
    subjects: list[str] = []
    yes: Any = None
    source: str | None = None
    source_text: str | None = None
    event_time: Timestamp | None = None
    deadline: Timestamp | None = None
    threshold: Any = None
    range: Any = None
    window: Any = None
    counted_play: str | None = None
    measure: Any = None
    void_rule: str | None = None
    ruleset: str | None = None
    extras: dict[str, Any] = {}


class Match(Model):
    """A Polymarket (``pm``) or Kalshi (``kx``) market matched to a HIP-4 market.

    ``bid``, ``ask`` and ``book`` are already in the HIP-4 market's side-0 terms, flipped where
    ``flipped`` is true, and None while that venue's book is not live. ``auto`` stays true until a
    reviewer confirms the grade. ``trading`` is False while the venue lists the market ahead of
    its open, as Kalshi does.
    """

    match_id: int
    venue: str
    id: str
    title: str
    yes_label: str | None = None
    no_label: str | None = None
    url: str | None = None
    grade: str
    auto: bool
    review_required: bool = False
    trading: bool = True
    flipped: bool
    diffs: list[Diff] = []
    rules: Rules | None = None
    rules_text: str | None = None
    book: Book | None = None
    book_ts: Timestamp | None = None
    bid: float | None = None
    ask: float | None = None

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


class MatchList(_Records, list[Match]):
    _item = Match

    def to_dataframe(self) -> "pd.DataFrame":
        from . import frames

        return frames.matches(self)


class QuestionSet(Model):
    """Sums over a question's named outcomes still trading.

    The gaps only mean something when ``complete`` is true: the named outcomes cover every result.
    """

    complete: bool
    buy_gap: float | None = None
    sell_gap: float | None = None
    sum_asks: float | None = None
    sum_bids: float | None = None
    sum_fair: float | None = None


class Question(Model, _Timed):
    """A multi-outcome question: its named outcomes, its fallback outcome, and the set sums."""

    question: int
    title: str
    template: str
    kind: str
    category: str
    deployer: str
    event_time: Timestamp | None
    deadline: Timestamp | None
    outcomes: MarketList
    fallback: Market | None
    set: QuestionSet

    @property
    def url(self) -> str:
        return f"{CONSOLE_URL}/q/{self.question}"

    def to_dataframe(self) -> "pd.DataFrame":
        """The question's outcomes, fallback included, one row each."""
        rows = MarketList(self.outcomes)
        if self.fallback is not None:
            rows.append(self.fallback)
        return rows.to_dataframe()


class QuestionList(_Records, list[Question]):
    _item = Question

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per question with its set sums."""
        from . import frames

        return frames.questions(self)


class ClusterMember(Model):
    summary: Market
    grade: str
    flipped: bool
    diffs: list[Diff] = []


class Cluster(Model):
    """The same question listed by several deployers.

    ``merged_book`` joins the books graded SAME, in the anchor market's side-0 prices;
    ``merged_book_with_close`` adds the CLOSE ones. ``clusters.list`` leaves both out.
    """

    id: str
    title: str
    kind: str
    anchor: int | None = None
    members: list[ClusterMember]
    merged_book: Book | None = None
    merged_book_with_close: Book | None = None

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per member market, with its grade against the anchor."""
        from . import frames

        return frames.cluster(self)


class ClusterList(_Records, list[Cluster]):
    _item = Cluster

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per member market of every cluster, indexed by outcome id."""
        from . import frames

        return frames.clusters(self)


class MarketDetail(Model):
    """One market in full: ``markets.get``.

    ``starts``, ``settles_at``, ``settles_by`` and ``url`` are the summary's, as on :class:`Market`.
    """

    summary: Market
    rules: Rules | None = None
    fields: dict[str, Any] = {}
    question_fields: dict[str, Any] | None = None
    fair: FairLine | None = None
    book: Book | None = None
    trades: TradeList = Field(default_factory=TradeList)
    question: Question | None = None
    cluster: Cluster | None = None
    matches: MatchList = Field(default_factory=MatchList)

    @property
    def outcome(self) -> int:
        return self.summary.outcome

    @property
    def title(self) -> str:
        return self.summary.title

    @property
    def starts(self) -> datetime | None:
        return self.summary.starts

    @property
    def settles_at(self) -> datetime | None:
        return self.summary.settles_at

    @property
    def settles_by(self) -> datetime | None:
        return self.summary.settles_by

    @property
    def url(self) -> str:
        return self.summary.url


class MarketDetails(dict[int, MarketDetail]):
    """Market details keyed by outcome id, in the order they were asked for."""

    def to_dataframe(self) -> "pd.DataFrame":
        """The markets' summaries, one row each."""
        return MarketList(d.summary for d in self.values()).to_dataframe()

    def matches_dataframe(self) -> "pd.DataFrame":
        """One row per matched Polymarket or Kalshi market, beside the HIP-4 price."""
        from . import frames

        return frames.venue_matches(self.values())


class FacetCount(Model):
    key: str
    n: int
    name: str | None = None


class Facets(Model):
    total: int
    categories: list[FacetCount]
    deployers: list[FacetCount]


class FeedStatus(Model):
    """One data feed. ``counts`` is sent by the feeds that count something, such as live books."""

    ok: bool
    last_update: Timestamp | None = Field(default=None, alias="lastUpdateMs")
    counts: dict[str, int | float] | None = None


class Status(Model):
    ready: bool
    markets: int
    clusters: int
    meta_at: Timestamp | None
    sources: dict[str, FeedStatus]


class HyperliquidQuote(Model):
    """The HIP-4 taker quote: average price, price per share after fees, shares filled.

    ``fees`` is the USD total for the whole order, trading fees only. Hyperliquid also takes
    ``settlement_fee_rate`` of a winning payout: ``settlement_fee`` is that fee in USD at the
    filled size, and ``held_all_in_px`` the price per dollar of payout after it, which is the
    price to set beside another venue's ``all_in_px`` and the one edges count.
    """

    avg_px: float | None
    all_in_px: float | None
    filled: float
    fees: float
    settlement_fee_rate: float | None = None
    settlement_fee: float | None = None
    held_all_in_px: float | None = None
    blocked: str | None


class VenueFill(Model):
    """A matched venue's quote. ``fee`` is the USD total for the whole order."""

    avg_px: float
    all_in_px: float
    fee: float
    filled: float


class VenueQuote(Model):
    venue: str
    id: str
    title: str
    url: str | None = None
    grade: str
    auto: bool
    quote: VenueFill | None
    blocked: str | None


class Comparison(Model):
    """The same order priced on HIP-4 and on every matched Polymarket and Kalshi market."""

    hl: HyperliquidQuote
    venues: list[VenueQuote]

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per venue, HIP-4 first."""
        from . import frames

        return frames.comparison(self)


class HistoryPoint(Model):
    """A market at one moment: its last trade as a side-0 price, and the fair line then in force.

    ``last_px`` is None before the day's first trade. The line's fields are None where none stood.
    """

    at: Timestamp
    last_px: float | None
    fair: float | None
    lo: float | None
    hi: float | None
    single_source: bool | None
    confidence: float | None


class History(_Records, list[HistoryPoint]):
    _item = HistoryPoint

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per point, oldest first, indexed by time."""
        from . import frames

        return frames.history(self)


class VenuePrint(Model):
    at: Timestamp
    p: float


class VenueLine(Model):
    """One venue's price over time for a market, as the price of the market's side 0.

    The line is the venue's best SAME or CLOSE match. ``state`` is ``ok``, ``empty`` (no prices
    in the window) or ``unavailable`` (the venue did not answer). ``as_of`` is the moment the
    prints were read up to.
    """

    venue: str
    id: str
    title: str
    url: str | None = None
    grade: str
    auto: bool
    flipped: bool
    state: str
    as_of: Timestamp | None
    prints: list[VenuePrint]


class VenueHistory(Model):
    """Matched Polymarket and Kalshi prices over a window: ``markets.venue_history``.

    ``since`` and ``step`` are the window served, which the API may move up or widen.
    """

    since: Timestamp = Field(alias="from")
    step: int
    lines: list[VenueLine]

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per price, each venue's oldest first."""
        from . import frames

        return frames.venue_history(self)


class Bar(Model):
    """One price bar. ``at`` is when it opens; prices are decimals from 0 to 1, volume contracts."""

    at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class BarList(list[Bar]):
    def to_dataframe(self) -> "pd.DataFrame":
        """One row per bar, oldest first, indexed by the bar's opening time."""
        from . import frames

        return frames.bars(self)


class Sheet(str):
    """CSV text from ``/sheets``, the same a spreadsheet's ``IMPORTDATA`` reads.

    ``header`` says whether the first line names the columns.
    """

    header: bool

    def __new__(cls, text: str, *, header: bool = True) -> "Sheet":
        sheet = super().__new__(cls, text)
        sheet.header = header
        return sheet

    def to_dataframe(self) -> "pd.DataFrame":
        """The sheet as a table. ``starts_utc`` and ``settles_by_utc`` become UTC datetimes."""
        from . import frames

        return frames.sheet(self)


class BoardStats(Model):
    """Scores over ``n`` resolved forecasts: the Brier score, and skill against two baselines.

    Skill is the baseline's mean Brier score less the forecaster's, over the ``n_fair`` (or
    ``n_market``) forecasts made while that baseline had a price: above 0 beats the baseline.
    ``skill_fair_low`` is ``skill_fair`` less two standard errors, which established rows rank by.
    """

    n: int
    brier: float | None
    n_fair: int
    skill_fair: float | None
    skill_fair_low: float | None
    n_market: int
    skill_market: float | None


class Baseline(BoardStats):
    """A baseline every forecaster is scored against: ``fair`` or ``market``."""

    key: str
    label: str


class BoardRow(BoardStats):
    """One forecaster on the board. ``early`` rows have too few resolved forecasts to rank."""

    rank: int
    forecaster: str
    early: bool


class ForecastBoard(Model):
    """The forecast record's board: ``forecasts.board``.

    ``as_of`` is when the newest daily record closed and ``through`` its day. ``total``,
    ``resolved`` and ``established`` count every row, ``rows`` holds one page of them.
    """

    as_of: datetime | None
    through: date | None
    records: int
    settled: int
    settlements_root: str
    baselines: list[Baseline]
    total: int
    resolved: int
    established: int
    rows: list[BoardRow]

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per forecaster on this page, indexed by rank."""
        from . import frames

        return frames.board(self)


class RecordDay(Model):
    """One closed day of the forecast record: how many lines, their hashes, and where they are."""

    day: date
    count: int
    root: str
    sha256: str
    bytes: int
    closed_at: datetime
    url: str


class RecordDayList(_Records, list[RecordDay]):
    _item = RecordDay

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per day, indexed by day."""
        from . import frames

        return frames.record_days(self)


class RecordLine(Model):
    """One signed forecast as the daily record holds it.

    Probabilities are basis points: 6,250 is 62.5%. ``fair_bps`` and ``mid_bps`` are the fair
    line and the book's mid when the forecast was accepted. ``batch``, ``index``, ``size``,
    ``type``, ``time`` and ``nonce`` rebuild the signed message, so anyone can check
    ``signature``.
    """

    id: int
    forecaster: str
    outcome: int
    probability_bps: int
    accepted_at: datetime
    cutoff: datetime
    fair_bps: int | None = None
    fair_note: str | None = None
    mid_bps: int | None = None
    book_time: datetime | None = None
    batch: int
    index: int
    size: int
    type: str
    time: int
    nonce: str
    signature: str


class RecordLineList(_Records, list[RecordLine]):
    _item = RecordLine

    def to_dataframe(self) -> "pd.DataFrame":
        """One row per forecast, indexed by its id, probabilities as decimals from 0 to 1."""
        from . import frames

        return frames.record_lines(self)
