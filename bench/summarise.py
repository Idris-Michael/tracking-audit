"""Turns batch runs into bench/results.json. Only what the runs recorded; nothing estimated.

uv run python bench/summarise.py run1 run2
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


def summarise(run: str) -> dict[str, Any]:
    outcomes = [json.loads(p.read_text("utf-8")) for p in sorted((OUT / run).glob("*.json"))]
    audited = [o for o in outcomes if o.get("report")]
    refused = [
        o for o in outcomes if not o.get("report") and "refuses automated browsers" in (o["error"] or "")
    ]
    other_errors = [o for o in outcomes if not o.get("report") and o not in refused]
    log = (OUT / f"{run}.log").read_text("utf-8")
    wall = re.search(r"wall (\d+)s", log)
    statuses: dict[str, Counter[str]] = {}
    for o in audited:
        for c in o["report"]["checks"]:
            statuses.setdefault(c["name"], Counter())[c["status"]] += 1
    return {
        "sites": len(outcomes),
        "audited": len(audited),
        "refused_automated_browser": len(refused),
        "other_errors": len(other_errors),
        "concurrency": 4,
        "wall_s": int(wall.group(1)) if wall else None,
        "median_s_per_audited_site": round(statistics.median(o["seconds"] for o in audited), 2)
        if audited
        else None,
        "scores": {o["target"]: o["report"]["score"] for o in audited},
        "check_results": {name: dict(c) for name, c in statuses.items()},
        "refused": [o["target"] for o in refused],
    }


def main() -> None:
    runs = sys.argv[1:] or ["run2"]
    results = {run: summarise(run) for run in runs}
    (HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n", "utf-8")
    print(
        json.dumps(
            {
                r: {k: v for k, v in s.items() if k in ("audited", "refused_automated_browser", "wall_s")}
                for r, s in results.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
