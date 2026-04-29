#!/usr/bin/env python3
"""GitHub Actions cron entry: refresh BOK MPC schedule cache."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bok_schedule import fetch_bok_mpc_schedule


def main() -> int:
    today = date.today()
    year = today.year

    print(f"[{today.isoformat()}] BOK MPC schedule refresh ({year})")
    bok = fetch_bok_mpc_schedule(year, force_refresh=True)
    print(f"  -> {len(bok)} BOK MPC events")

    if today.month >= 10:
        nxt = year + 1
        print(f"Last quarter -- also refreshing {nxt}")
        bok_next = fetch_bok_mpc_schedule(nxt, force_refresh=True)
        print(f"  -> {len(bok_next)} BOK MPC events for {nxt}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
