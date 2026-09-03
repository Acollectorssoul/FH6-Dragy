#!/usr/bin/env python3
"""
fh6_merge.py - combine everyone's runs into one leaderboard.

    python3 fh6_merge.py leaderboard.json ~/fh6_draggy_runs.json bassim.json
    python3 fh6_merge.py leaderboard.json enzo=mine.json bassim=his.json

Then view it like any other history:

    python3 fh6_web.py --runs leaderboard.json

Runs already carry a driver name if the tool was run with --driver. For files
that predate that, name them on the command line as above and the driver is
filled in from there.

Duplicates are dropped on timestamp plus driver, so re-merging an updated file
does not double anyone's runs. Nothing is overwritten in the source files -
this only ever writes the output.

Red-lit runs are kept, since they are part of an honest record, but they are
already excluded from bests everywhere they would flatter someone.
"""

import json
import os
import sys


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except OSError as e:
        sys.exit(f"cannot read {path}: {e}")
    except json.JSONDecodeError as e:
        sys.exit(f"{path} is not valid JSON: {e}")
    if not isinstance(data, list):
        sys.exit(f"{path} does not contain a list of runs")
    return data


def key(run):
    """What makes a run unique - the same instant by the same driver."""
    return (run.get("driver", ""), run.get("timestamp", ""),
            round(float((run.get("et") or {}).get("1/4mi", {}).get("s") or 0), 3))


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip().split("\n\n")[1].strip())
    out_path, sources = sys.argv[1], sys.argv[2:]

    merged, seen, counts = [], set(), {}
    for src in sources:
        name = ""
        if "=" in src and not os.path.exists(src):
            name, src = src.split("=", 1)
        elif "=" in src.split(os.sep)[-1] and not os.path.exists(src):
            name, src = src.split("=", 1)
        runs = load(os.path.expanduser(src))
        added = 0
        for r in runs:
            if name and not r.get("driver"):
                r["driver"] = name
            who = r.get("driver") or "unknown"
            k = key(r)
            if k in seen:
                continue
            seen.add(k)
            merged.append(r)
            counts[who] = counts.get(who, 0) + 1
            added += 1
        print(f"  {os.path.basename(src):<32}{added:>4} new "
              f"of {len(runs)}")

    merged.sort(key=lambda r: r.get("timestamp") or "")
    try:
        with open(os.path.expanduser(out_path), "w") as f:
            json.dump(merged, f, indent=1)
    except OSError as e:
        sys.exit(f"cannot write {out_path}: {e}")

    print(f"\n{len(merged)} runs -> {out_path}\n")
    print(f"  {'driver':<20}{'runs':>6}{'best 1/4':>11}{'best 60ft':>11}")
    for who in sorted(counts):
        mine = [r for r in merged if (r.get("driver") or "unknown") == who
                and not r.get("foul")]
        def best(key_):
            vals = [(r.get("et") or {}).get(key_, {}).get("s") for r in mine]
            vals = [v for v in vals if isinstance(v, (int, float))]
            return f"{min(vals):.3f}" if vals else "-"
        print(f"  {who:<20}{counts[who]:>6}{best('1/4mi'):>11}{best('60ft'):>11}")
    print(f"\n  view it:  python3 fh6_web.py --runs {out_path}\n")


if __name__ == "__main__":
    main()
