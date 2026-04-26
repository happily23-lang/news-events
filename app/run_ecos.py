#!/usr/bin/env python3
"""GitHub Actions cron entry: refresh ECOS indicator cache (registered indicators only)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ecos_client import INDICATORS, fetch_indicator, load_ecos_key


def main() -> int:
    if not load_ecos_key():
        print("ERROR: ECOS_API_KEY not found in env or .env", file=sys.stderr)
        return 1

    for indicator_id, spec in INDICATORS.items():
        rows = fetch_indicator(
            stat_code=spec["stat_code"],
            freq=spec["freq"],
            n=12,
            item_codes=spec["items"],
        )
        print(f"  {indicator_id} ({spec['label']}): {len(rows)} rows")

    return 0


if __name__ == "__main__":
    sys.exit(main())
