# Changelog

Notable changes to `hyperodds`, newest first. Versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

### Added

- `Hyperodds`, a client for the public Hyperodds API: markets with filters and sorting, one market
  in full or many in batches of ten, books, trades, newest listings, edges, facets, questions,
  clusters, venue comparisons and API status.
- `ho.charts.bars()`, open, high, low, close and volume over a market's whole history, in bars
  from one minute to one month.
- `ho.markets.history()` and `ho.markets.venue_history()`: the last day's trades and fair line,
  and the matched Polymarket and Kalshi prices over up to 120 days.
- `ho.sheets`, the CSV that spreadsheets read, and `ho.forecasts`, the forecast record's board,
  closed days and signed forecasts.
- `ho.live.listings()`, new listings as they appear, resuming across the API's ten-minute stream
  limit.
- `to_dataframe()` on every list and on books, questions, clusters, comparisons, sheets and the
  forecast board, and `matches_dataframe()` to set HIP-4 beside each matched Polymarket and Kalshi
  market.
- Pacing below the API's request budget, and retries that wait as long as a 429 asks.
- `notebooks/hip4-in-colab.ipynb`, every live HIP-4 market in pandas from Google Colab.

[0.1.0]: https://github.com/usehyperodds/hyperodds-python/releases/tag/v0.1.0
