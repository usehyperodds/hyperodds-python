# hyperodds

<p>
  <a href="https://pypi.org/project/hyperodds/"><img alt="PyPI" src="https://img.shields.io/pypi/v/hyperodds?color=176646&label=pypi"></a>
  <a href="https://github.com/usehyperodds/hyperodds-python/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/usehyperodds/hyperodds-python/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://colab.research.google.com/github/usehyperodds/hyperodds-python/blob/main/notebooks/hip4-in-colab.ipynb"><img alt="Open in Colab" src="https://colab.research.google.com/assets/colab-badge.svg"></a>
  <a href="https://github.com/usehyperodds/hyperodds-python/blob/main/LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-176646"></a>
  <a href="https://usehyperodds.com"><img alt="Website" src="https://img.shields.io/badge/web-usehyperodds.com-070b0c"></a>
  <a href="https://x.com/UseHyperodds"><img alt="X" src="https://img.shields.io/badge/@UseHyperodds-000000?logo=x&logoColor=white"></a>
</p>

Every HIP-4 outcome market on Hyperliquid, in Python. `hyperodds` reads the public Hyperodds API
and returns every deployer's markets as typed objects or pandas DataFrames, with price bars from
one minute to one month and the Polymarket and Kalshi markets matched to each. There is no key to
set up.

```sh
pip install "hyperodds[pandas]"
```

```python
from hyperodds import Hyperodds

with Hyperodds() as ho:
    markets = ho.markets.list().to_dataframe()
    busiest = markets.volume_24h.idxmax()
    bars = ho.charts.bars(busiest, "1d", limit=7).to_dataframe()

print(markets[["title", "mid", "fair", "volume_24h"]].head().to_string())
print("\nDaily bars:", markets.at[busiest, "title"])
print(bars)
```

```
                                               title       mid      fair  volume_24h
outcome                                                                             
11492    NBA 2026/2027 winner: Oklahoma City Thunder  0.124545       NaN   2099246.0
11493        NBA 2026/2027 winner: San Antonio Spurs  0.210105       NaN   1740067.0
11495       NBA 2026/2027 winner: Philadelphia 76ers  0.107395       NaN   1575867.0
11497           NBA 2026/2027 winner: Boston Celtics  0.081285       NaN   1443232.0
1488                  NFL 2027 winner: Buffalo Bills  0.154255  0.157715   1304896.0

Daily bars: NBA 2026/2027 winner: Oklahoma City Thunder
                              open     high      low    close     volume
at                                                                      
2026-11-19 00:00:00+00:00  0.12962  0.13108  0.12961  0.13082   213008.0
2026-11-20 00:00:00+00:00  0.13082  0.13108  0.13081  0.13090   203385.0
2026-11-21 00:00:00+00:00  0.13090  0.13900  0.13090  0.13886   262942.0
2026-11-22 00:00:00+00:00  0.13887  0.13887  0.13458  0.13458   280529.0
2026-11-23 00:00:00+00:00  0.13402  0.13402  0.13184  0.13189   313498.0
2026-11-24 00:00:00+00:00  0.13189  0.13189  0.13087  0.13150   494913.0
2026-11-25 00:00:00+00:00  0.13149  0.13150  0.12439  0.12455  1994951.0
```

Python 3.10 or later. Without the `pandas` extra you get the typed objects and everything except
`to_dataframe()`.

## Try it in Google Colab

[`notebooks/hip4-in-colab.ipynb`](https://github.com/usehyperodds/hyperodds-python/blob/main/notebooks/hip4-in-colab.ipynb) loads every live market into
pandas and sets HIP-4 prices beside Polymarket for each question both list.
[Open it in Colab](https://colab.research.google.com/github/usehyperodds/hyperodds-python/blob/main/notebooks/hip4-in-colab.ipynb)
and run all cells; it needs no sign-in beyond Colab's own.

## What you can read

Calls on `markets`, `questions`, `clusters`, `quotes`, `meta` and `live` are named after the API
procedure they make. `charts`, `sheets` and `forecasts` read the chart feed, the spreadsheet CSV
and the forecast record.

| Call | Returns |
| --- | --- |
| `ho.markets.list(category=, deployer=, q=, sort=, settles_before=, min_edge_pts=, min_volume=, min_depth_usd=, limit=, include_settled=)` | Markets, most traded first. `sort` is `volume`, `edge`, `new` or `settling` |
| `ho.markets.get(outcome)` | One market in full: rules, fair line with its sources, book, last 50 trades, its question and cluster, matched Polymarket and Kalshi markets |
| `ho.markets.get_many(outcomes)` | `get` for many markets, ten to a request, keyed by outcome id in the order asked; ids the API does not hold are left out |
| `ho.markets.book(outcome, side=0)` | The order book in either side's prices; None before it loads, and always for a question's fallback outcome, which has no book |
| `ho.markets.trades(outcome, limit=)` | Trades from the last day, newest first |
| `ho.markets.newest(limit=)` | The newest listings |
| `ho.markets.edges(limit=, min_confidence=)` | Markets with a buy edge of half a point or more after fees, biggest first, on lines that are not single-source and books that trade |
| `ho.markets.facets()` | Market counts by category and deployer |
| `ho.markets.history(outcome, since=, until=, every=)` | The last trade and the fair line over the last day, one point per interval |
| `ho.markets.venue_history(outcome, since, step=60)` | The matched Polymarket and Kalshi prices over up to 120 days, as prices of the market's first side |
| `ho.charts.bars(outcome, interval="1h", side=0, start=, end=, limit=)` | Open, high, low, close and volume over a market's whole history, in bars from `1m` to `1M` |
| `ho.questions.list()`, `ho.questions.get(question)` | Multi-outcome questions with the sums of their books |
| `ho.clusters.list()`, `ho.clusters.get(id)` | The same question listed by several deployers, with merged books |
| `ho.quotes.compare(outcome, side=, action=, shares=)` | One order priced on HIP-4 and on each matched venue, trading fees included |
| `ho.meta.status()` | Whether the API is ready, and the health of each data feed |
| `ho.sheets.markets(columns=, ...)`, `ho.sheets.edges()`, `ho.sheets.market(outcome)`, `ho.sheets.question(question)` | The CSV a spreadsheet's `IMPORTDATA` reads, as text with a `to_dataframe()` |
| `ho.forecasts.board()`, `ho.forecasts.records()`, `ho.forecasts.day(day)` | The forecast record: the board of forecasters, its closed days, and one day's signed forecasts |
| `ho.live.listings(last_event_id=)` | New listings as they appear, resuming across reconnects |
| `ho.query(procedure, input)` | Any other public query, as plain JSON |

`sort=` orders `markets.list` four ways:

- `volume` (the default): most traded first.
- `edge`: in two tiers. The markets `edges()` would keep come first, those whose line is not
  single-source, has confidence of 0.5 or more and sits on a book that trades; every other market
  follows. Each tier runs biggest edge first, so a big edge on a weak line ranks below a smaller
  one on a line `edges()` trusts.
- `new`: newest listing first.
- `settling`: soonest first by each market's next time: `starts` while that is ahead, then
  `settles_at` or `settles_by`. A game that has started counts from its `settles_by`.

Results are [pydantic](https://docs.pydantic.dev) models with the API's field names in snake case
(`volume24h` becomes `volume_24h`). Fields the API adds after this release are kept on each
model's `model_extra` under the API's own camelCase names, so a new `newField` reads as
`market.model_extra["newField"]`.

## DataFrames

`to_dataframe()` works on every list and on a book, a cluster, a question, a comparison, a
sheet and the forecast board. Times come back as UTC `datetime64` columns and ids that can be
missing as nullable `Int64`.

| From | One row per | Index |
| --- | --- | --- |
| `markets.list()`, `newest()`, `edges()` | market, with its book, fair line, best edge, cluster and settlement columns flattened | `outcome` |
| `markets.get_many(...)` | market (`.to_dataframe()`), or matched venue market (`.matches_dataframe()`) | `outcome`, or none (0 to n-1) for matches |
| `clusters.list()` | member market of every cluster, with its grade against the anchor | `outcome` |
| `book.to_dataframe()` | price level, bids then asks, with cumulative shares | |
| `markets.trades(...)` | trade | `tid` |
| `questions.list()` | question, with the sums of its named outcomes' books | `question` |
| `quotes.compare(...)` | venue, HIP-4 first, with the price per share after fees | |
| `charts.bars(...)` | bar, oldest first | `at`, the bar's opening time |
| `markets.history(...)` | moment, with the last trade and the fair line then | `at` |
| `markets.venue_history(...)` | venue price, each venue's oldest first | |
| `sheets.markets()`, `edges()`, `market()`, `question()` | market, under the sheet's own column names | |
| `forecasts.board()` | forecaster on the page | `rank` |
| `forecasts.day(day)` | signed forecast, probabilities as decimals | `id` |

## HIP-4 beside Polymarket and Kalshi

```python
markets = ho.markets.list().to_dataframe()
matched = markets[markets.match_grade.isin(["SAME", "CLOSE"])]
venues = ho.markets.get_many(matched.index).matches_dataframe()
```

`venues` has one row per Polymarket (`pm`) or Kalshi (`kx`) market matched to a HIP-4 market, with
both books as the price of the HIP-4 market's first side and `gap_pts`, the HIP-4 mid less the
venue's, in points. `grade` says how far to trust the pairing: SAME markets settle on the same
terms, CLOSE ones differ in the rule fields that `differences` names (a price source, a cutoff
time), DIFFERENT ones ask another question. Keep pairs with a tight spread on both sides
(`hl_spread`, `venue_spread`): a book with only a bid at 0.01 and an ask at 0.99 has a mid of 0.50
whatever the market thinks. Kalshi lists each team of a game as its own market, so one HIP-4 game
can have two Kalshi rows, one of them flipped; keep one per `outcome` and `venue` before counting
pairs. [`examples/compare_venues.py`](https://github.com/usehyperodds/hyperodds-python/blob/main/examples/compare_venues.py) does this
end to end.

For one order at a given size, `ho.quotes.compare(outcome, side=0, action="buy", shares=100)`
walks each venue's book and adds that venue's trading fees: `all_in_px` is the price per share
after them, and `fees` their USD total for the whole order. Hyperliquid also takes a fee from a
winning payout at settlement, so compare venues on `held_all_in_px`, the price per dollar of
payout. On the HIP-4 row it sits a little above `all_in_px`; Polymarket and Kalshi charge only on
the trade, so theirs are equal. Edges count the same price.

## Price history

```python
from datetime import datetime, timedelta, timezone

since = datetime.now(timezone.utc) - timedelta(days=7)
bars = ho.charts.bars(outcome, "4h", start=since).to_dataframe()
venues = ho.markets.venue_history(outcome, since, step=240).to_dataframe()
```

`charts.bars` reads the feed behind the console's charts. It holds every trade a market has had,
settled markets included, and serves bars of `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `6h`, `12h`
and `1d`; `1w` (from Monday) and `1M` bars are built from the daily ones. A bar exists only where
something traded. Leave out `start` and `limit` to read the whole history, 5,000 bars a request.
`side=1` gives the second side's prices.

`markets.venue_history` returns one line per venue: its best SAME or CLOSE match, already in the
HIP-4 market's side-0 terms, at most one price every `step` minutes (1, 5, 15, 60 or 240). The
venues print at their own moments, so pivot and resample before setting them beside the bars.
[`examples/price_history.py`](https://github.com/usehyperodds/hyperodds-python/blob/main/examples/price_history.py)
prints a week of all three.

`markets.history` covers the last day at finer grain, with the fair line and its band at each
point.

## New listings as they appear

```python
for batch in ho.live.listings():
    for market in batch:
        print(market.outcome, market.title, market.url)
```

The API ends each stream after ten minutes; the client reconnects and resumes from the last
listing it yielded. Pass `last_event_id` (an outcome id) to start with every live market listed
after it. The stream keeps its own timeouts, whatever `timeout=` says: 10 seconds to connect and
60 seconds between events. The API pings every 15 seconds, so a silent minute means the link is
gone, and the client reconnects. [`examples/watch_listings.py`](https://github.com/usehyperodds/hyperodds-python/blob/main/examples/watch_listings.py) prints listings until you stop
it.

## Reading the numbers

- Prices and probabilities are decimals from 0 to 1 for a market's first side, `side0` (usually
  Yes). The second side trades at one minus that. `change_24h` is a decimal price change like
  them: 0.03 is 3 points. A book read with `markets.book(outcome, side=1)` is in side-1 prices,
  and its `book_side` column says so.
- Edges and gaps are percentage points: an edge of 1.5 is 1.5¢ a share. Edges are after every fee,
  including the one Hyperliquid takes from a winning payout at settlement, for a $100 order, and
  None where $100 would not fill.
- Bars are decimals from 0 to 1 as well. The chart feed itself sends cents, which `charts.bars`
  converts; sheets already hold decimals. The forecast record keeps probabilities in basis
  points (`probability_bps`), and its DataFrame gives them as decimals.
- `volume_24h` counts contracts, each worth $1 at settlement. `depth_*_usd` is the dollar value
  resting within 5 points of the mid.
- Times read as the console shows them, in UTC. `starts` is a game's start or a decision's time
  (the console says "Starts", or "Decision" for `policy_rate` markets), and the market settles
  after it. `settles_at` is when a price market settles at a set price time. Every other market
  has `settles_by`, the latest it settles; that includes touch markets (`price_touch`), which have
  no single price time and settle by their deadline, as the console shows. `event_time` and
  `deadline` are the raw times behind them, and `settles_before=` filters on `settles_at`, else
  `settles_by`.
- Fair lines are estimates. Price markets read Deribit's options surfaces, realized volatility on
  Hyperliquid perps, or both (`fair_method` is `options`, `realized-vol` or `blend`); other
  markets read matched Polymarket and Kalshi quotes (`consensus`). Check `fair_confidence` and
  `fair_single_source` before acting on one.
- `fair_single_source` is True when a line rests on one input: one venue's quote for a
  `consensus` line, or realized volatility alone. An `options` or `blend` line is False even with
  Deribit as its only options venue: a surface prices every strike, so it counts as more than one
  source. `edges()` keeps only lines that are not single-source, with confidence of 0.5 or more
  (`min_confidence=`) and a book quoting both sides within 10 points.

## Limits, retries and errors

The API allows each IP address 30 calls a second with bursts of 120, and counts each call in a
batch. The client paces itself below that: 20 calls a second with bursts of 60 by default, shared
by everything one `Hyperodds` instance sends. `rate_limit=` changes it; `None` turns it off.
Four reads have tighter budgets of their own: the chart feed takes 5 requests a second with
bursts of 40, the forecast record 10 with bursts of 60, sheets 20 with bursts of 100, and
`markets.venue_history` one a second after a burst of ten. A loop that outruns one of them is
slowed by the retries below.

A call the API refuses with 429 is retried after the wait the API names, up to 60 seconds. A 503
(the API is still loading markets after a restart, or the chart feed is reading a long history
for the first time) is retried for up to about 20 seconds. Any
other 5xx except 501 and 505, and a network error, is retried with a shorter exponential backoff.
`markets.get_many` reads a market whose read failed with a 5xx once more after the rest. Each
retry is logged on the `hyperodds` logger, as a warning when the wait is 5 seconds or more. After
`max_retries` (default 3) the error is raised:

| Error | When |
| --- | --- |
| `NotFoundError` | No market, question, cluster or record day has that id |
| `RateLimitError` | The budget is still spent after the retries, or the API asks for a wait over 60 seconds; `retry_after` says how long |
| `APIError` | Any other refusal, with `status`, `code` (`BAD_REQUEST`, ...) and the API's message, or an answer this release cannot read |
| `APIConnectionError` | The API could not be reached or did not answer in time |

All four derive from `HyperoddsError`.

## Configuration

```python
Hyperodds(
    base_url=None,  # or HYPERODDS_API_URL, else https://api.usehyperodds.com
    timeout=30.0,  # seconds per request; live.listings keeps its own timeouts
    max_retries=3,
    rate_limit=20.0,  # calls a second; None to turn pacing off
    http_client=None,  # your own httpx.Client, for proxies or a mock transport
)
```

Use it as a context manager (`with Hyperodds() as ho:`) or call `ho.close()` when you are done;
a closed client raises `HyperoddsError`. Inputs that can't be sent, such as a NaN, a float or a
string where an id belongs, or `settles_before` in seconds, raise `ValueError` or `TypeError`
before any request.
httpx reads `HTTPS_PROXY` from the environment.

## Development

```sh
git clone https://github.com/usehyperodds/hyperodds-python.git
cd hyperodds-python
uv sync
uv run pytest
```

`uv run ruff check`, `uv run ruff format --check` and `uv run mypy` run the other checks CI runs.
The tests replay answers recorded from production in `tests/fixtures`;
`uv run pytest -m live` runs the smoke tests against the API itself. See
[CONTRIBUTING.md](https://github.com/usehyperodds/hyperodds-python/blob/main/CONTRIBUTING.md)
before opening a pull request.

## Security

Report vulnerabilities privately to security@usehyperodds.com, or through
[the repository's Security tab](https://github.com/usehyperodds/hyperodds-python/security/advisories/new).
Please do not open a public issue for them.
[SECURITY.md](https://github.com/usehyperodds/hyperodds-python/blob/main/SECURITY.md) has the
details.

## License

[MIT](https://github.com/usehyperodds/hyperodds-python/blob/main/LICENSE). Third-party names
identify data sources and platforms. Hyperodds is not affiliated with Hyperliquid, Polymarket,
Kalshi, Deribit or Google.

<p align="center">
  <a href="https://usehyperodds.com">Website</a> ·
  <a href="https://usehyperodds.com/console">Console</a> ·
  <a href="https://x.com/UseHyperodds">X</a> ·
  <a href="https://github.com/usehyperodds">GitHub</a>
</p>
