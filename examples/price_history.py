"""A week of one market's price on HIP-4, Polymarket and Kalshi, in four-hour steps.

pip install "hyperodds[pandas]"
python examples/price_history.py
"""

from datetime import datetime, timedelta, timezone

from hyperodds import Hyperodds

since = datetime.now(timezone.utc) - timedelta(days=7)

with Hyperodds() as ho:
    matched = [m for m in ho.markets.list() if m.match_grade == "SAME"]
    market = max(matched, key=lambda m: m.volume_24h)
    hip4 = ho.charts.bars(market.outcome, "4h", start=since).to_dataframe()
    venues = ho.markets.venue_history(market.outcome, since, step=240).to_dataframe()

# The venues print at their own moments: keep each one's last price in every four hours, which
# is what a bar's close is.
prices = venues.pivot(index="at", columns="venue", values="p").resample("4h").last()
prices.insert(0, "hip4", hip4.close)

print(market.title, market.url)
print(prices.dropna(how="all").tail(12))
