#!/usr/bin/env python3
"""
fh6_launch.py - what actually makes your launches quick.

    python3 fh6_launch.py

Reads your saved run history and, across every run, relates the 60 foot time
to the two things you control at the line: tyre temperature and launch rpm.

A real Draggy cannot do this. It sees the time and nothing else, so a cold-tyre
run and a hot-tyre run just look like inconsistency. Here both are measured, so
the scatter resolves into a curve - and the top of that curve is the setup you
are looking for.

Needs runs recorded with a version that logs tyre temp, and enough of them to
mean something. It will say so rather than drawing conclusions from four runs.
"""

import json
import os
import sys

HISTORY = os.path.expanduser("~/fh6_draggy_runs.json")


def bucket(runs, key, edges, label):
    """Group runs by a variable and show the 60ft in each band."""
    have = [r for r in runs if isinstance(r.get(key), (int, float))
            and (r.get("et") or {}).get("60ft", {}).get("s")]
    if len(have) < 6:
        print(f"  {label}: only {len(have)} runs carry this - need at least 6\n")
        return
    print(f"  {label}")
    print(f"    {'band':<18}{'runs':>6}{'best 60ft':>12}{'median':>10}")
    rows = []
    for lo, hi in zip(edges, edges[1:]):
        grp = [r for r in have if lo <= r[key] < hi]
        if not grp:
            continue
        sixty = sorted((r["et"]["60ft"]["s"] for r in grp))
        med = sixty[len(sixty) // 2]
        rows.append((lo, hi, len(grp), sixty[0], med))
        print(f"    {f'{lo:g} - {hi:g}':<18}{len(grp):>6}{sixty[0]:>12.3f}{med:>10.3f}")
    if len(rows) >= 2:
        best = min(rows, key=lambda r: r[4])
        worst = max(rows, key=lambda r: r[4])
        gain = worst[4] - best[4]
        print(f"\n    Quickest band: {best[0]:g}-{best[1]:g}  "
              f"(median {best[4]:.3f}s)")
        print(f"    Worst band:    {worst[0]:g}-{worst[1]:g}  "
              f"(median {worst[4]:.3f}s)")
        print(f"    Difference: {gain:.3f}s at 60ft\n")
    else:
        print()


def main():
    if not os.path.exists(HISTORY):
        sys.exit(f"no run history at {HISTORY} - drive some runs first")
    runs = json.load(open(HISTORY))
    runs = [r for r in runs if (r.get("et") or {}).get("60ft", {}).get("s")]
    if not runs:
        sys.exit("no runs with a 60ft time yet")

    sixty = sorted(r["et"]["60ft"]["s"] for r in runs)
    print(f"\n{len(runs)} runs   60ft from {sixty[0]:.3f} to {sixty[-1]:.3f}s "
          f"(spread {sixty[-1]-sixty[0]:.3f}s)\n")

    bucket(runs, "tire_temp",
           [0, 60, 80, 100, 120, 140, 160, 200, 400], "BY TYRE TEMPERATURE")
    bucket(runs, "launch_rpm",
           [0, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 12000], "BY LAUNCH RPM")

    spin = [r for r in runs if isinstance(r.get("peak_wheelspin"), (int, float))]
    if len(spin) >= 6:
        clean = [r for r in spin if r["peak_wheelspin"] <= 1.0]
        loose = [r for r in spin if r["peak_wheelspin"] > 1.0]
        print("  BY TRACTION")
        for name, grp in (("gripped (slip <= 1.0)", clean),
                          ("spun (slip > 1.0)", loose)):
            if grp:
                s = sorted(r["et"]["60ft"]["s"] for r in grp)
                print(f"    {name:<24}{len(grp):>4} runs   "
                      f"best {s[0]:.3f}   median {s[len(s)//2]:.3f}")
        print()

    missing = sum(1 for r in runs if r.get("tire_temp") is None)
    if missing:
        print(f"  {missing} of {len(runs)} runs predate tyre-temp logging and are")
        print("  excluded from the temperature comparison.\n")


if __name__ == "__main__":
    main()
