from __future__ import annotations

import json
import logging
import math
import operator
import os
import random
import re
import threading
import time
import urllib.request
from collections.abc import Generator, Iterable, Iterator, Sequence
from datetime import date, datetime, timedelta, timezone
from numbers import Real
from types import TracebackType
from typing import Any, Literal, TypeVar, cast

import httpx
from pydantic import TypeAdapter, ValidationError

from ._version import __version__
from .errors import (
    APIConnectionError,
    APIError,
    HyperoddsError,
    NotFoundError,
    RateLimitError,
    error_from,
)
from .models import (
    Bar,
    BarList,
    Book,
    Cluster,
    ClusterList,
    Comparison,
    Facets,
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
    Status,
    TradeList,
    VenueHistory,
)

DEFAULT_BASE_URL = "https://api.usehyperodds.com"
USER_AGENT = f"hyperodds-python/{__version__} httpx/{httpx.__version__}"

BATCH_SIZE = 10
MAX_RETRY_AFTER = 60.0
LOUD_WAIT = 5.0
STREAM_TIMEOUT = httpx.Timeout(10.0, read=60.0)

Sort = Literal["volume", "edge", "new", "settling"]
Side = Literal[0, 1]
Action = Literal["buy", "sell"]
Step = Literal[1, 5, 15, 60, 240]
Interval = Literal["1m", "5m", "15m", "30m", "1h", "4h", "6h", "12h", "1d", "1w", "1M"]

# The chart feed's name for each interval. It serves bars up to a day; weeks and months are
# built here from daily bars, as charting libraries build them.
_RESOLUTIONS: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "6h": "360",
    "12h": "720",
    "1d": "1D",
    "1w": "1D",
    "1M": "1D",
}
_DAYS_PER_BAR = {"1w": 7, "1M": 31}
MAX_BARS = 5000

log = logging.getLogger("hyperodds")
T = TypeVar("T")


class _Pacer:
    """A token bucket shared by everything one client sends. Each call in a batch takes a token."""

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self.burst = max(1.0, rate * 3)
        self._tokens = self.burst
        self._at = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: int = 1) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst, self._tokens + (now - self._at) * self.rate)
            self._at = now
            self._tokens -= n
            wait = -self._tokens / self.rate
        if wait > 0:
            time.sleep(wait)


class Hyperodds:
    """Client for the Hyperodds public API: every HIP-4 market on Hyperliquid, read-only.

    No key is needed. Calls are grouped by what they read: ``markets``, ``questions``,
    ``clusters``, ``quotes``, ``meta`` and ``live``, each named after the API procedure it calls
    (``ho.markets.list()`` calls ``markets.list``). ``charts``, ``sheets`` and ``forecasts`` read
    the chart feed, the spreadsheet CSV and the forecast record.

    The client keeps under the API's request budget on its own, ``rate_limit`` calls a second with
    bursts of three seconds' worth (the API allows 30 a second per address, bursts of 120). It
    retries a refused or failed call up to ``max_retries`` times, waiting as long as the API asks
    on a 429. ``base_url`` defaults to ``HYPERODDS_API_URL`` from the environment, then to
    ``https://api.usehyperodds.com``. Pass ``http_client`` to bring your own ``httpx.Client``
    (proxies, a mock transport); you close it yourself.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        rate_limit: float | None = 20.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        url = (base_url or os.environ.get("HYPERODDS_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL as e:
            raise ValueError(f"base_url is not a URL: {url!r}") from e
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise ValueError(f"base_url needs https:// or http:// and a host, got {url!r}")
        if timeout <= 0:
            raise ValueError("timeout is in seconds and must be above 0")
        if max_retries < 0:
            raise ValueError("max_retries can't be negative")
        if rate_limit is not None and rate_limit <= 0:
            raise ValueError("rate_limit is calls a second above 0, or None to turn pacing off")
        self.base_url = url
        self.max_retries = max_retries
        self._pacer = None if rate_limit is None else _Pacer(rate_limit)
        self._own_http = http_client is None
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)), follow_redirects=True
        )
        self._headers = {"user-agent": USER_AGENT, "accept": "application/json"}
        self._closed = False

        self.markets = Markets(self)
        self.questions = Questions(self)
        self.clusters = Clusters(self)
        self.quotes = Quotes(self)
        self.meta = Meta(self)
        self.live = Live(self)
        self.charts = Charts(self)
        self.sheets = Sheets(self)
        self.forecasts = Forecasts(self)

    def __repr__(self) -> str:
        return f"Hyperodds(base_url={self.base_url!r})"

    def __enter__(self) -> Hyperodds:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True
        if self._own_http:
            self._http.close()

    def _check_open(self) -> None:
        if self._closed or self._http.is_closed:
            raise HyperoddsError(
                "This Hyperodds client is closed. Create a new one to keep reading."
            )

    def query(self, procedure: str, input: dict[str, Any] | None = None) -> Any:
        """Call any public query by name and return its result as plain JSON.

        For procedures this package has no method for yet. ``input`` uses the API's own field
        names (``{"outcome": 3600, "limit": 5}``).
        """
        params = {"input": _dumps(input)} if input is not None else {}
        response, body = self._send(procedure, params, calls=1)
        if not response.is_success:
            raise _error(response, body)
        try:
            return body["result"]["data"]
        except (KeyError, TypeError) as e:
            raise APIError(
                _describe(response), status=response.status_code, procedure=procedure
            ) from e

    def _batch(self, procedure: str, inputs: list[dict[str, Any]]) -> list[Any]:
        """Run the same query for each input in one request. Failed calls come back as errors."""
        params = {"batch": "1", "input": _dumps({str(i): x for i, x in enumerate(inputs)})}
        response, body = self._send(",".join([procedure] * len(inputs)), params, len(inputs))
        if not isinstance(body, list) or len(body) != len(inputs):
            raise _error(response, body)
        results: list[Any] = []
        for entry in body:
            if isinstance(entry, dict) and "result" in entry:
                results.append(entry["result"].get("data"))
            else:
                results.append(error_from(response.status_code, entry))
        return results

    def _send(self, path: str, params: dict[str, str], calls: int) -> tuple[httpx.Response, Any]:
        name = path.partition(",")[0]
        label = name if calls == 1 else f"{name} ({calls} calls)"
        response = self._request(f"/trpc/{path}", params, calls, label, self._headers)
        return response, _json(response)

    def _get(self, path: str, params: dict[str, Any], accept: str) -> httpx.Response:
        """Read a path outside tRPC: the chart feed, a sheet or the forecast record."""
        sent = {k: str(v) for k, v in params.items() if v is not None}
        headers = {**self._headers, "accept": accept}
        response = self._request(path, sent, 1, path.lstrip("/"), headers)
        if not response.is_success:
            raise _plain_error(response, path.lstrip("/"))
        return response

    def _request(
        self, path: str, params: dict[str, str], calls: int, label: str, headers: dict[str, str]
    ) -> httpx.Response:
        self._check_open()
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            if self._pacer:
                self._pacer.take(calls)
            try:
                response = self._http.get(url, params=params, headers=headers)
            except httpx.TransportError as e:
                if attempt >= self.max_retries:
                    raise APIConnectionError(self._failure(e, self._http.timeout.read)) from e
                delay = _backoff(attempt)
                _note_retry(f"{label} failed ({e!r})", delay)
            else:
                if not _retryable(response.status_code) or attempt >= self.max_retries:
                    return response
                wait = _retry_after(response)
                if wait is not None and wait > MAX_RETRY_AFTER:
                    return response
                delay = wait if wait is not None else _backoff(attempt, response.status_code)
                _note_retry(f"{label} answered {response.status_code}", delay)
            attempt += 1
            time.sleep(delay)

    def _failure(self, e: httpx.TransportError, read_timeout: float | None) -> str:
        """Describe a failed request, telling a server that went quiet from one never reached."""
        proxy = self._proxy()
        if proxy:
            through = f" through the proxy at {proxy}"
        else:
            through = " through its proxy" if isinstance(e, httpx.ProxyError) else ""
        if isinstance(e, httpx.ReadTimeout) and read_timeout is not None:
            return (
                f"Reached {self.base_url}{through}, but it did not answer within "
                f"{read_timeout:g} s ({e!r})"
            )
        return f"Could not reach {self.base_url}{through}: {e!r}"

    def _proxy(self) -> str | None:
        """The proxy the environment sends API requests through, without its credentials."""
        if not self._http.trust_env:
            return None
        url = httpx.URL(self.base_url)
        proxies = urllib.request.getproxies()
        proxy = proxies.get(url.scheme) or proxies.get("all")
        if not proxy or urllib.request.proxy_bypass(url.host):
            return None
        at = httpx.URL(proxy if "://" in proxy else f"http://{proxy}")
        return f"{at.scheme}://{at.host}" + (f":{at.port}" if at.port else "")

    def _stream(self, procedure: str, input: dict[str, Any] | None) -> Iterator[tuple[str, Any]]:
        """Open one live stream and yield ``(event id, payload)`` for each message on it."""
        self._check_open()
        params = {"input": _dumps(input)} if input is not None else {}
        headers = {**self._headers, "accept": "text/event-stream"}
        if self._pacer:
            self._pacer.take()
        url = f"{self.base_url}/trpc/{procedure}"
        with self._http.stream(
            "GET", url, params=params, headers=headers, timeout=STREAM_TIMEOUT
        ) as response:
            if response.status_code != 200:
                response.read()
                raise _error(response, _json(response))
            for event, data, event_id in _sse_events(response.iter_lines()):
                if event == "serialized-error":
                    raise error_from(200, {"error": json.loads(data)}, _retry_after(response))
                if event == "message":
                    yield event_id, json.loads(data)


class _Namespace:
    def __init__(self, client: Hyperodds) -> None:
        self._client = client


class Markets(_Namespace):
    def list(
        self,
        *,
        category: str | None = None,
        deployer: str | None = None,
        q: str | None = None,
        sort: Sort | None = None,
        settles_before: datetime | date | float | None = None,
        min_edge_pts: float | None = None,
        min_volume: float | None = None,
        min_depth_usd: float | None = None,
        limit: int | None = None,
        include_settled: bool = False,
    ) -> MarketList:
        """Every HIP-4 market that matches, most traded first unless ``sort`` says otherwise.

        ``category`` is one of ``sports``, ``crypto``, ``equities``, ``commodities``,
        ``indices``, ``macro``, ``ipo``, ``awards`` or ``other``; ``deployer`` a deployer code
        such as ``hl``, ``out``, ``txyz`` or ``skew`` (``markets.facets()`` lists the live ones).
        ``q`` searches titles, labels, subcategories and deployer names. ``settles_before`` keeps
        markets whose ``settles_at``, else ``settles_by``, is at or before it: a datetime (naive
        ones read as UTC), a date (midnight UTC) or milliseconds since the epoch.
        ``min_edge_pts`` is the best buy edge after fees, in points. ``include_settled`` adds
        markets settled or removed in the last six hours.

        ``sort``:

        - ``volume`` (default): most traded first.
        - ``edge``: in two tiers. Markets :meth:`edges` would keep come first (a line that is not
          single-source with confidence 0.5 or more, and a book that trades), then every other
          market; each tier biggest edge first.
        - ``new``: newest listing first.
        - ``settling``: soonest first by each market's next time, ``starts`` while it is ahead,
          then ``settles_at`` or ``settles_by``. A live game counts from its ``settles_by``.
        """
        data = self._client.query(
            "markets.list",
            _input(
                category=category,
                deployer=deployer,
                q=q,
                sort=sort,
                settlesBefore=_ms(settles_before, "settles_before"),
                minEdgePts=_finite(min_edge_pts, "min_edge_pts"),
                minVolume=_finite(min_volume, "min_volume"),
                minDepthUsd=_finite(min_depth_usd, "min_depth_usd"),
                limit=limit,
                includeSettled=include_settled or None,
            ),
        )
        return _validate(MarketList, data, "markets.list")

    def get(self, outcome: int) -> MarketDetail:
        """One market in full: rules, fair line with its sources, book, the last 50 trades, its
        question and cluster, and the Polymarket and Kalshi markets matched to it.

        Raises :class:`NotFoundError` for an outcome the API does not hold.
        """
        data = self._client.query("markets.get", {"outcome": _id(outcome, "outcome")})
        return _validate(MarketDetail, data, "markets.get")

    def get_many(self, outcomes: Iterable[int]) -> MarketDetails:
        """:meth:`get` for many markets, ten to a request, keyed by outcome id in the order given.

        Outcomes the API no longer holds, such as a market removed after the list was read, are
        left out without raising. A market whose read fails with a 5xx is read once more
        after the rest; any failure left raises once every batch has been read.
        """
        wanted = list(dict.fromkeys(_id(o, "outcome") for o in outcomes))
        results = self._get_each(wanted)
        again = [
            o for o in wanted if isinstance(results[o], APIError) and _retryable(results[o].status)
        ]
        if again:
            time.sleep(_backoff(0))
            results.update(self._get_each(again))
        details = MarketDetails()
        for outcome in wanted:
            result = results[outcome]
            if isinstance(result, NotFoundError):
                continue
            if isinstance(result, APIError):
                raise result
            details[outcome] = _validate(MarketDetail, result, "markets.get")
        return details

    def _get_each(self, outcomes: Sequence[int]) -> dict[int, Any]:
        results: dict[int, Any] = {}
        for start in range(0, len(outcomes), BATCH_SIZE):
            chunk = outcomes[start : start + BATCH_SIZE]
            answers = self._client._batch("markets.get", [{"outcome": o} for o in chunk])
            results.update(zip(chunk, answers, strict=True))
        return results

    def book(self, outcome: int, side: Side = 0) -> Book | None:
        """The order book in ``side``'s prices; None before it loads and for fallback outcomes."""
        data = self._client.query(
            "markets.book", {"outcome": _id(outcome, "outcome"), "side": side}
        )
        return None if data is None else _validate(Book, {**data, "side": side}, "markets.book")

    def trades(self, outcome: int, limit: int | None = None) -> TradeList:
        """Trades from the last day, newest first. ``limit`` 1 to 500, default 100."""
        data = self._client.query(
            "markets.trades", _input(outcome=_id(outcome, "outcome"), limit=limit)
        )
        return _validate(TradeList, data, "markets.trades")

    def newest(self, limit: int | None = None) -> MarketList:
        """The newest listings, newest first. ``limit`` 1 to 200, default 50."""
        data = self._client.query("markets.newest", _input(limit=limit))
        return _validate(MarketList, data, "markets.newest")

    def edges(self, limit: int | None = None, min_confidence: float | None = None) -> MarketList:
        """Markets whose best buy edge after fees is at least half a point, biggest first.

        A market counts when its fair line is not single-source (an options surface counts as
        more than one source; one venue's quote or realized volatility alone does not), reaches
        ``min_confidence`` (default 0.5), and its book quotes both sides within 10 points with
        depth near the mid. ``min_confidence=0`` lifts only the confidence bar: the other three
        still apply. ``limit`` 1 to 100, default 20.
        """
        data = self._client.query(
            "markets.edges",
            _input(limit=limit, minConfidence=_finite(min_confidence, "min_confidence")),
        )
        return _validate(MarketList, data, "markets.edges")

    def facets(self) -> Facets:
        """How many markets each category and deployer has, largest first."""
        return _validate(Facets, self._client.query("markets.facets"), "markets.facets")

    def history(
        self,
        outcome: int,
        *,
        since: datetime | date | float | None = None,
        until: datetime | date | float | None = None,
        every: timedelta | int | None = None,
    ) -> History:
        """A market's last trade and fair line over the last day, one point per interval.

        ``since`` and ``until`` take what ``settles_before`` takes; the window starts at most 24
        hours back and ends now unless ``until`` says otherwise. ``every`` is the interval, a
        timedelta or milliseconds, from one minute to one hour; left out, the API picks one that
        gives about 240 points.
        """
        if isinstance(every, timedelta):
            every = round(every.total_seconds() * 1000)
        data = self._client.query(
            "markets.history",
            _input(
                outcome=_id(outcome, "outcome"),
                since=_ms(since, "since"),
                until=_ms(until, "until"),
                every=every,
            ),
        )
        return _validate(History, data, "markets.history")

    def venue_history(
        self, outcome: int, since: datetime | date | float, step: Step = 60
    ) -> VenueHistory:
        """The matched Polymarket and Kalshi prices since ``since``, as side-0 prices.

        One line per venue, its best SAME or CLOSE match, with at most one price every ``step``
        minutes: 1, 5, 15, 60 or 240. ``since`` takes what ``settles_before`` takes and reaches
        back 120 days at most; the answer names the window served. The API allows one of these a
        second after a burst of ten.
        """
        data = self._client.query(
            "markets.venueHistory",
            {"outcome": _id(outcome, "outcome"), "from": _ms(since, "since"), "step": step},
        )
        return _validate(VenueHistory, data, "markets.venueHistory")


class Questions(_Namespace):
    def list(self) -> QuestionList:
        """Every multi-outcome question with an outcome still trading."""
        return _validate(QuestionList, self._client.query("questions.list"), "questions.list")

    def get(self, question: int) -> Question:
        """One question, by ``question_id`` as found on a market."""
        data = self._client.query("questions.get", {"question": _id(question, "question")})
        return _validate(Question, data, "questions.get")


class Clusters(_Namespace):
    def list(self) -> ClusterList:
        """Questions listed by more than one deployer, with their members. No merged books."""
        return _validate(ClusterList, self._client.query("clusters.list"), "clusters.list")

    def get(self, id: str) -> Cluster:
        """One cluster with its merged books, by ``cluster_id`` as found on a market."""
        data = self._client.query("clusters.get", {"id": id})
        return _validate(Cluster, data, "clusters.get")


class Quotes(_Namespace):
    def compare(self, outcome: int, *, side: Side, action: Action, shares: float) -> Comparison:
        """Price the same order on HIP-4 and on every matched Polymarket and Kalshi market.

        Each quote walks that venue's book for ``shares`` and adds its trading fees: ``all_in_px``
        is the price per share after them. Hyperliquid also takes a fee from a winning payout at
        settlement, so compare on ``hl.held_all_in_px``, the HIP-4 price per dollar of payout,
        which is what edges count.
        """
        data = self._client.query(
            "quotes.compare",
            {
                "outcome": _id(outcome, "outcome"),
                "side": side,
                "action": action,
                "shares": _finite(shares, "shares"),
            },
        )
        return _validate(Comparison, data, "quotes.compare")


class Meta(_Namespace):
    def status(self) -> Status:
        """Whether the API is ready, how many markets it holds, and the health of each feed."""
        return _validate(Status, self._client.query("meta.status"), "meta.status")


class Live(_Namespace):
    def listings(self, last_event_id: int | str | None = None) -> Generator[MarketList, None, None]:
        """Yield new HIP-4 listings as they appear, a list at a time, until you stop iterating.

        With ``last_event_id``, an outcome id, it first yields every live market listed after
        that one. Without it, it starts from the newest listing now. The API ends each stream
        after ten minutes; this reconnects and resumes from the last listing it yielded, so
        nothing is missed in between. A listing can arrive twice across a reconnect: key them by
        ``outcome``. The stream keeps its own timeouts, whatever the client's ``timeout``: 10 s to
        connect and 60 s between events (the API pings every 15 s).
        """
        client = self._client
        if last_event_id is None:
            last = None
        elif isinstance(last_event_id, str):
            if not re.fullmatch(r"[0-9]{1,15}", last_event_id):
                raise ValueError(f"last_event_id is an outcome id, got {last_event_id!r}")
            last = last_event_id
        else:
            last = str(_id(last_event_id, "last_event_id"))
        if last is None:
            newest = client.markets.newest(limit=1)
            last = str(newest[0].outcome) if newest else None
        attempt = 0
        while True:
            try:
                for event_id, data in client._stream(
                    "live.listings", {"lastEventId": last} if last else None
                ):
                    attempt = 0
                    last = event_id or last
                    yield _validate(MarketList, data, "live.listings")
                # The API closes every stream after ten minutes; a clean close resets the retries.
                attempt, delay = 0, 1.0
            except (httpx.TransportError, APIError) as e:
                status = e.status if isinstance(e, APIError) else None
                if status is not None and not _retryable(status):
                    raise
                if attempt >= client.max_retries:
                    if isinstance(e, APIError):
                        raise
                    why = client._failure(e, STREAM_TIMEOUT.read)
                    raise APIConnectionError(f"Lost the listings stream. {why}") from e
                wait = e.retry_after if isinstance(e, RateLimitError) else None
                if wait is not None and wait > MAX_RETRY_AFTER:
                    raise
                delay = wait if wait is not None else _backoff(attempt, status)
                attempt += 1
                _note_retry(f"listings stream dropped ({e!r})", delay)
            time.sleep(delay)


class Charts(_Namespace):
    def bars(
        self,
        outcome: int,
        interval: Interval = "1h",
        *,
        side: Side = 0,
        start: datetime | date | float | None = None,
        end: datetime | date | float | None = None,
        limit: int | None = None,
    ) -> BarList:
        """Open, high, low, close and volume for one side of a market, oldest bar first.

        Read from the chart feed, which holds a market's whole trading history, settled markets
        included. ``interval`` runs from ``1m`` to ``1M``: ``1m``, ``5m``, ``15m``, ``30m``,
        ``1h``, ``4h``, ``6h``, ``12h``, ``1d``, ``1w`` (from Monday) and ``1M``. Bars start on
        the minute in UTC and exist only where something traded.

        ``start`` and ``end`` take what ``settles_before`` takes; ``end`` is now when left out and
        is not itself included. ``limit`` keeps the newest bars. With neither ``start`` nor
        ``limit`` the whole history is read, 5,000 bars a request. Prices are decimals from 0 to
        1 like every other price here (the feed itself sends cents); volume counts contracts.

        Raises :class:`NotFoundError` for an outcome the feed has never held.
        """
        if interval not in _RESOLUTIONS:
            raise ValueError(f"interval is one of {', '.join(_RESOLUTIONS)}, got {interval!r}")
        if limit is not None and limit < 1:
            raise ValueError("limit is a number of bars, 1 or more")
        if side not in (0, 1):
            raise ValueError(f"side is 0 or 1, got {side!r}")
        first, last = _ms(start, "start"), _ms(end, "end")
        until = math.ceil((last if last is not None else time.time() * 1000) / 1000)
        since = None if first is None else _period_start(first // 1000, interval)
        if since is not None and since >= until:
            raise ValueError("start must be before end")
        days = _DAYS_PER_BAR.get(interval, 1)
        wanted = None if limit is None else limit * days
        symbol = f"HIP4:{_id(outcome, 'outcome') * 10 + side}"

        bars: list[Bar] = []
        while True:
            ask = MAX_BARS if wanted is None else min(MAX_BARS, wanted - len(bars))
            params: dict[str, Any] = {
                "symbol": symbol,
                "resolution": _RESOLUTIONS[interval],
                "to": until,
            }
            if since is None:
                params["countback"] = ask
            else:
                params["from"] = since
            body = _json(self._client._get("/udf/history", params, "application/json"))
            page = _bars(body)
            bars[:0] = page
            # The feed answers with the newest bars it has before `to`; a full page means more.
            full = len(page) >= (ask if since is None else MAX_BARS)
            if not full or (wanted is not None and len(bars) >= wanted):
                break
            until = int(page[0].at.timestamp())
            if since is not None and since >= until:
                break
        if days > 1:
            bars = _roll_up(bars, interval)
        return BarList(bars if limit is None else bars[-limit:])


class Sheets(_Namespace):
    """The CSV that spreadsheets read, one row per market. Prices are decimals from 0 to 1."""

    def markets(
        self,
        *,
        columns: Sequence[str] | None = None,
        header: bool = True,
        category: str | None = None,
        deployer: str | None = None,
        q: str | None = None,
        sort: Sort | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sheet:
        """Live markets, most traded first, up to 1,000 rows; fallback outcomes are left out.

        ``columns`` picks and orders them (``outcome``, ``title``, ``bid``, ``ask``, ``mid``,
        ``fair``, ``edge_pts`` and so on; left out, all of them). The filters are those of
        ``markets.list``; ``deployer`` also takes a deployer's name.
        """
        return self._read(
            "markets.csv",
            columns,
            header,
            category=category,
            deployer=deployer,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    def edges(
        self, *, columns: Sequence[str] | None = None, header: bool = True, limit: int | None = None
    ) -> Sheet:
        """The markets ``markets.edges`` keeps, biggest edge first. ``limit`` 1 to 100."""
        return self._read("edges.csv", columns, header, limit=limit)

    def market(
        self, outcome: int, *, columns: Sequence[str] | None = None, header: bool = True
    ) -> Sheet:
        """One market's row. ``columns=["mid"], header=False`` is the bare number."""
        return self._read(f"markets/{_id(outcome, 'outcome')}.csv", columns, header)

    def question(
        self, question: int, *, columns: Sequence[str] | None = None, header: bool = True
    ) -> Sheet:
        """The named outcomes of one question, a row each."""
        return self._read(f"questions/{_id(question, 'question')}.csv", columns, header)

    def _read(
        self, file: str, columns: Sequence[str] | None, header: bool, **filters: Any
    ) -> Sheet:
        if isinstance(columns, str):
            raise TypeError(f'columns is a list of names, such as ["mid"], got {columns!r}')
        params = {
            "columns": ",".join(columns) if columns else None,
            "header": None if header else "0",
            **filters,
        }
        response = self._client._get(f"/sheets/{file}", params, "text/csv")
        return Sheet(response.text, header=header)


class Forecasts(_Namespace):
    """The public forecast record: signed forecasts, scored when their markets settle."""

    def board(self, limit: int | None = None, offset: int | None = None) -> ForecastBoard:
        """Forecasters ranked by skill against the fair line, with the two baselines.

        ``limit`` 1 to 200, default 50; ``offset`` pages through the rows.
        """
        response = self._client._get(
            "/forecasts/board", {"limit": limit, "offset": offset}, "application/json"
        )
        return _validate(ForecastBoard, _json(response), "forecasts/board")

    def records(self) -> RecordDayList:
        """Every closed day of the record, with its line count and hashes."""
        body = _json(self._client._get("/forecasts/records", {}, "application/json"))
        days = body.get("days") if isinstance(body, dict) else None
        return _validate(RecordDayList, days, "forecasts/records")

    def day(self, day: date | str) -> RecordLineList:
        """One closed day's forecasts as the record holds them, signatures included.

        ``day`` is a date or ``YYYY-MM-DD``. Raises :class:`NotFoundError` for a day with no
        record: one still open, or one on which nothing was forecast.
        """
        if isinstance(day, datetime):
            day = day.date()
        name = day.isoformat() if isinstance(day, date) else day
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", name):
            raise ValueError(f"day is a date or YYYY-MM-DD, got {day!r}")
        path = f"forecasts/records/{name}.jsonl"
        response = self._client._get(f"/{path}", {}, "application/x-ndjson")
        try:
            lines = [json.loads(line) for line in response.text.splitlines() if line]
        except ValueError as e:
            raise APIError(_describe(response), status=response.status_code, procedure=path) from e
        return _validate(RecordLineList, lines, path)


def _bars(body: Any) -> list[Bar]:
    """The feed's columns as bars. It sends cents and seconds; bars carry 0 to 1 and UTC times."""
    status = body.get("s") if isinstance(body, dict) else None
    if status == "no_data":
        return []
    try:
        if status != "ok":
            raise ValueError(status)
        columns = [body[key] for key in "tohlcv"]
        return [
            Bar(
                at=datetime.fromtimestamp(t, tz=timezone.utc),
                open=round(o / 100, 6),
                high=round(h / 100, 6),
                low=round(lo / 100, 6),
                close=round(c / 100, 6),
                volume=v,
            )
            for t, o, h, lo, c, v in zip(*columns, strict=True)
        ]
    except (KeyError, TypeError, ValueError, ValidationError) as e:
        raise APIError(
            f"The chart feed's answer does not fit hyperodds {__version__}. "
            "A newer release may read it.",
            status=200,
            procedure="udf/history",
        ) from e


def _period_start(seconds: int, interval: str) -> int:
    """Seconds moved back to the start of their week or month, so its first bar is whole."""
    if interval not in _DAYS_PER_BAR:
        return seconds
    return int(_period(datetime.fromtimestamp(seconds, tz=timezone.utc), interval).timestamp())


def _period(at: datetime, interval: str) -> datetime:
    day = at.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday()) if interval == "1w" else day.replace(day=1)


def _roll_up(days: Sequence[Bar], interval: str) -> list[Bar]:
    bars: list[Bar] = []
    for day in days:
        at = _period(day.at, interval)
        if bars and bars[-1].at == at:
            bar = bars[-1]
            bars[-1] = Bar(
                at=at,
                open=bar.open,
                high=max(bar.high, day.high),
                low=min(bar.low, day.low),
                close=day.close,
                volume=bar.volume + day.volume,
            )
        else:
            bars.append(day.model_copy(update={"at": at}))
    return bars


def _sse_events(lines: Iterable[str]) -> Iterator[tuple[str, str, str]]:
    """Parse server-sent events into ``(event, data, id)``; events without data are skipped."""
    event, event_id = "message", ""
    data: list[str] = []
    for line in lines:
        if not line:
            if data:
                yield event, "\n".join(data), event_id
            event, event_id, data = "message", "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id":
            event_id = value
    if data:
        yield event, "\n".join(data), event_id


_adapters: dict[Any, TypeAdapter[Any]] = {}


def _validate(tp: type[T], data: Any, procedure: str) -> T:
    """Read an answer into its model, turning pydantic's ValidationError into an APIError."""
    adapter = _adapters.get(tp)
    if adapter is None:
        adapter = _adapters[tp] = TypeAdapter(tp)
    try:
        return cast(T, adapter.validate_python(data))
    except ValidationError as e:
        first, count = e.errors()[0], e.error_count()
        where = ".".join(str(part) for part in first["loc"]) or "the answer"
        problems = "1 problem" if count == 1 else f"{count} problems"
        raise APIError(
            f"The answer does not fit hyperodds {__version__} ({problems}; "
            f"first, {where}: {first['msg']}). A newer release may read it.",
            status=200,
            procedure=procedure,
        ) from e


def _input(**fields: Any) -> dict[str, Any] | None:
    sent = {k: v for k, v in fields.items() if v is not None}
    return sent or None


def _id(value: Any, name: str) -> int:
    """An id as an int. numpy and pandas integers pass; a float, a string or a bool is refused."""
    if not isinstance(value, bool):
        try:
            return operator.index(value)
        except TypeError:
            pass
    raise TypeError(f"{name} must be a whole number, got {value!r}")


def _finite(value: Any, name: str) -> Any:
    """A number to send. NaN and infinity are refused here: JSON can't carry them."""
    if isinstance(value, Real) and not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return value


def _ms(value: Any, name: str) -> int | None:
    """A time as the API takes it, milliseconds since the epoch."""
    if value is None:
        return None
    given = value
    if str(getattr(value, "dtype", "")).startswith("datetime64"):
        value = value.astype("datetime64[ms]").item()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        ms = int(value.timestamp() * 1000)
    elif isinstance(value, date):
        midnight = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        ms = int(midnight.timestamp() * 1000)
    elif isinstance(value, Real) and not isinstance(value, bool):
        number = float(_finite(value, name))
        # Seconds passed by mistake would read as January 1970 and filter out every market.
        if 0 < number < 1e11:
            raise ValueError(
                f"{name} takes milliseconds since the epoch; {value!r} looks like seconds"
            )
        ms = int(number)
    else:
        raise TypeError(f"{name} takes a datetime or milliseconds, got {given!r}")
    if ms <= 0:
        raise ValueError(f"{name} must be after 1970-01-01 00:00 UTC, got {given!r}")
    return ms


def _dumps(value: Any) -> str:
    try:
        return json.dumps(value, separators=(",", ":"), default=_plain, allow_nan=False)
    except ValueError as e:
        if "Out of range float" not in str(e):
            raise
        raise ValueError(f"NaN and infinity can't be sent to the API: {value!r}") from e


def _plain(value: Any) -> Any:
    """numpy scalars, which pandas hands out, as the Python numbers JSON knows."""
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"{type(value).__name__} can't be sent to the API")


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error(response: httpx.Response, body: Any) -> APIError:
    return error_from(
        response.status_code, body, _retry_after(response), fallback=_describe(response)
    )


def _plain_error(response: httpx.Response, path: str) -> APIError:
    """The error for a refusal outside tRPC, whose bodies are a sentence or a small object."""
    body = _json(response)
    message, code = None, None
    if isinstance(body, dict):
        message = body.get("errmsg") or body.get("error")
        code = body.get("code")
    elif response.headers.get("content-type", "").startswith("text/plain"):
        message = response.text.strip()
    message = str(message) if message else _describe(response)
    status = response.status_code
    if status == 404:
        return NotFoundError(message, status=status, code=code, procedure=path)
    if status == 429:
        return RateLimitError(
            message, status=status, code=code, procedure=path, retry_after=_retry_after(response)
        )
    return APIError(message, status=status, code=code, procedure=path)


def _describe(response: httpx.Response) -> str:
    """What came back instead of an API answer: its type, Cloudflare's ray id, its first words."""
    kind = response.headers.get("content-type", "no content type").partition(";")[0]
    if ray := response.headers.get("cf-ray"):
        kind += f", cf-ray {ray}"
    text = " ".join(response.text[:500].split())[:200]
    return f"Unexpected answer ({kind}): {text!r}" if text else f"Unexpected answer ({kind}, empty)"


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def _note_retry(what: str, delay: float) -> None:
    # Short waits stay quiet; a long one is a warning, so a notebook cell never hangs unexplained.
    level = logging.WARNING if delay >= LOUD_WAIT else logging.INFO
    log.log(level, "hyperodds: %s, retrying in %.1fs", what, delay)


def _retryable(status: int) -> bool:
    # 501 and 505 say the request itself can't be served; every other 5xx can pass.
    return status == 429 or (status >= 500 and status not in (501, 505))


def _backoff(attempt: int, status: int | None = None) -> float:
    # A 503 means the markets are still loading after a restart, which takes a few seconds.
    base = 3.0 if status == 503 else 0.5
    return min(30.0, base * 2.0**attempt) * random.uniform(0.5, 1.0)
