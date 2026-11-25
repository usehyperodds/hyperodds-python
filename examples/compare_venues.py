"""HIP-4 beside Polymarket and Kalshi for every matched market with a live book on both sides.

pip install "hyperodds[pandas]"
python examples/compare_venues.py
"""

import pandas as pd

from hyperodds import Hyperodds

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 40)

with Hyperodds() as ho:
    markets = ho.markets.list().to_dataframe()
    matched = markets[markets.match_grade.isin(["SAME", "CLOSE"])]
    venues = ho.markets.get_many(matched.index).matches_dataframe()

venues = venues[venues.grade.isin(["SAME", "CLOSE"])]
venues = venues[(venues.hl_spread <= 0.05) & (venues.venue_spread <= 0.05)]
# Kalshi lists each team of a game as its own market, and both match the same HIP-4 market:
# keep one pair per HIP-4 market and venue, the best graded with the tightest book.
venues = venues.sort_values(["grade", "venue_spread"], ascending=[False, True])
venues = venues.drop_duplicates(["outcome", "venue"])
summary = venues.groupby(["venue", "grade"]).gap_pts.agg(
    pairs="size", median_gap=lambda g: g.abs().median()
)
print(summary, "\n")
print(
    venues[["title", "venue", "grade", "differences", "hl_mid", "venue_mid", "gap_pts"]]
    .sort_values("gap_pts", key=abs, ascending=False)
    .head(10)
    .to_string(index=False)
)
