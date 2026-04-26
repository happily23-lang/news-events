#!/usr/bin/env python3
"""GitHub Actions cron entry: fetch DART target disclosures and update cache."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dart_disclosure import fetch_dart_target_events, load_dart_key


def main() -> int:
    api_key = load_dart_key()
    if not api_key:
        print("ERROR: DART_API_KEY not found in env or .env", file=sys.stderr)
        return 1

    events = fetch_dart_target_events(api_key)
    print(f"Fetched {len(events)} DART target disclosures")

    by_type: dict[str, int] = {}
    for e in events:
        t = e.get("disclosure_type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t}: {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
