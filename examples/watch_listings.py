"""Print each new HIP-4 market as it lists. Stop with Ctrl-C.

pip install hyperodds
python examples/watch_listings.py
"""

from hyperodds import Hyperodds

with Hyperodds() as ho:
    print("Waiting for new HIP-4 listings...")
    try:
        for batch in ho.live.listings():
            for m in batch:
                when = f"{m.listed_at:%H:%M:%S} UTC" if m.listed_at else "--:--:-- UTC"
                print(f"{when}  {m.outcome:>6}  {m.deployer_name:<12} {m.title}  {m.url}")
    except KeyboardInterrupt:
        pass
