"""Every live HIP-4 market in a DataFrame, and a week of daily bars for the most traded one.

pip install "hyperodds[pandas]"
python examples/quickstart.py
"""

from hyperodds import Hyperodds

with Hyperodds() as ho:
    markets = ho.markets.list().to_dataframe()
    busiest = markets.volume_24h.idxmax()
    bars = ho.charts.bars(busiest, "1d", limit=7).to_dataframe()

print(markets[["title", "mid", "fair", "volume_24h"]].head().to_string())
print("\nDaily bars:", markets.at[busiest, "title"])
print(bars)
