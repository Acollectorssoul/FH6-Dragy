#!/usr/bin/env python3
"""
fh6_verify.py - check that saved times match the telemetry they came from.

    python3 fh6_verify.py                    your own history
    python3 fh6_verify.py leaderboard.json   a merged leaderboard

For every run it checks two things:

  1. the stored telemetry still hashes to what was recorded at the time, so
     neither the packets nor the result have been edited since
  2. re-deriving the ET from those packets reproduces the time that was saved

A run that passes both was produced by the tool from real packets. That does
not make it uncheatable - a replayed capture would still pass - but it means
a posted time can be checked rather than taken on trust, and it catches the
easy case of someone opening the JSON and typing a quicker number.

Runs recorded before evidence was kept are reported as unverifiable rather
than as failures. They are not wrong, they just cannot be checked.
"""

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fh6_draggy as D

EVIDENCE = os.path.expanduser("~/fh6_runs")


def redo(path):
    """Re-run the analysis over saved packets and return the fresh run dict."""
    rows = []
    with open(path) as f:
        import csv as _csv
        for r in _csv.DictReader(f):
            try:
                s = {k: float(v) for k, v in r.items() if v not in ("", None)}
            except (TypeError, ValueError):
                continue
            s["t"] = s.pop("t", 0.0)
            rows.append(s)
    if len(rows) < 20:
        return None
    # CSV gives everything back as float; the analysis indexes lists with
    # some of these, so integer fields have to be restored as integers.
    for name, typ in D._FIELDS:
        for s in rows:
            s.setdefault(name, 0)
            if typ in "iIHBb":
                s[name] = int(s[name])
    return D.analyze(rows, None, None, None)


def main():
    hist = os.path.expanduser(sys.argv[1]) if len(sys.argv) > 1 else D.HISTORY_PATH
    try:
        runs = json.load(open(hist))
    except OSError as e:
        sys.exit(f"cannot read {hist}: {e}")

    print(f"\nchecking {len(runs)} runs from {hist}\n")
    print(f"{'#':>4}{'driver':<14}{'saved 1/4':>11}{'re-derived':>12}"
          f"{'hash':>8}{'verdict':>14}")
    ok = bad = skipped = 0
    for i, r in enumerate(runs, 1):
        who = (r.get("driver") or "-")[:13]
        saved = ((r.get("et") or {}).get("1/4mi") or {}).get("s")
        ev = r.get("evidence")
        if not ev:
            skipped += 1
            print(f"{i:>4}{who:<14}"
                  f"{(f'{saved:.3f}' if saved else '-'):>11}"
                  f"{'-':>12}{'-':>8}{'no evidence':>14}")
            continue
        path = os.path.join(EVIDENCE, ev)
        if not os.path.exists(path):
            bad += 1
            print(f"{i:>4}{who:<14}{(f'{saved:.3f}' if saved else '-'):>11}"
                  f"{'-':>12}{'MISSING':>8}{'FAIL':>14}")
            continue
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        hash_ok = digest == r.get("evidence_sha256")
        fresh = redo(path)
        got = ((fresh or {}).get("et") or {}).get("1/4mi", {}).get("s")
        match = (saved is not None and got is not None
                 and abs(saved - got) < 0.01)
        good = hash_ok and match
        ok += good
        bad += not good
        print(f"{i:>4}{who:<14}"
              f"{(f'{saved:.3f}' if saved else '-'):>11}"
              f"{(f'{got:.3f}' if got else '-'):>12}"
              f"{('ok' if hash_ok else 'EDITED'):>8}"
              f"{('verified' if good else 'FAIL'):>14}")

    print(f"\n  {ok} verified, {bad} failed, {skipped} unverifiable\n")
    if bad:
        print("  A failure means the saved time does not follow from the saved")
        print("  packets, or one of the two has been edited since.\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
