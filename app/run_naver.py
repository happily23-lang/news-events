#!/usr/bin/env python3
"""GitHub Actions cron entry: refresh Naver supply flow cache for previously-tracked codes."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from naver_supply import _CACHE_PATH, fetch_supply_flow


def _extract_unique_codes() -> list[str]:
    if not os.path.exists(_CACHE_PATH):
        return []
    with open(_CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)
    codes: set[str] = set()
    for key in cache.keys():
        parts = key.split("|")
        if parts and parts[0]:
            codes.add(parts[0])
    return sorted(codes)


def main() -> int:
    codes = _extract_unique_codes()
    if not codes:
        print("No tracked codes in cache. Skipping.")
        return 0

    print(f"Refreshing {len(codes)} stock codes")

    ok = 0
    fail = 0
    for code in codes:
        result = fetch_supply_flow(code, days=5)
        if result:
            ok += 1
        else:
            fail += 1

    print(f"  ok={ok}  fail={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
