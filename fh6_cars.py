#!/usr/bin/env python3
"""
fh6_cars.py - give car ordinals real names.

    python3 fh6_cars.py                          list cars seen in your runs
    python3 fh6_cars.py 3141 "Koenigsegg Jesko"  name one
    python3 fh6_cars.py --import cars.csv        bulk import ordinal,name

The telemetry identifies a car only by its ordinal - an integer, no name
anywhere in the packet. Names therefore live in a local file that everything
else reads: slips, the web interface, leaderboards.

Communities publish ordinal-to-name lists for each Forza title once the game
has been out a while; --import takes any CSV whose first two columns are
ordinal and name. Until then, naming the handful of cars you actually drive
takes a minute.
"""

import csv
import json
import os
import sys

NAMES_PATH = os.path.expanduser("~/fh6_car_names.json")
HISTORY = os.path.expanduser("~/fh6_draggy_runs.json")


def load_names():
    try:
        with open(NAMES_PATH) as f:
            return {str(k): v for k, v in json.load(f).items()}
    except (OSError, json.JSONDecodeError):
        return {}


def save_names(names):
    with open(NAMES_PATH, "w") as f:
        json.dump(names, f, indent=1, sort_keys=True)


def car_name(ordinal, names=None):
    """The name if known, otherwise 'Car <ordinal>'."""
    if not ordinal:
        return "---"
    names = load_names() if names is None else names
    return names.get(str(ordinal), f"Car {ordinal}")


def seen_in_history():
    try:
        runs = json.load(open(HISTORY))
    except (OSError, json.JSONDecodeError):
        return {}
    seen = {}
    for r in runs:
        o = r.get("car_ordinal")
        if o:
            seen[o] = seen.get(o, 0) + 1
    return seen


def main():
    names = load_names()
    args = sys.argv[1:]

    if args and args[0] == "--import":
        if len(args) < 2:
            sys.exit("--import needs a CSV file")
        added = 0
        with open(os.path.expanduser(args[1])) as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                o, name = row[0].strip(), row[1].strip()
                if not o.isdigit() or not name or name.lower() == "name":
                    continue
                if names.get(o) != name:
                    names[o] = name
                    added += 1
        save_names(names)
        print(f"{added} names imported, {len(names)} total")
        return

    if len(args) >= 2:
        ordinal = args[0]
        if not ordinal.isdigit():
            sys.exit(f"'{ordinal}' is not an ordinal")
        names[ordinal] = " ".join(args[1:])
        save_names(names)
        print(f"Car {ordinal} = {names[ordinal]}")
        return

    seen = seen_in_history()
    if not seen and not names:
        print("no cars seen yet - drive some runs first")
        return
    print(f"\n  {'ordinal':>9}  {'runs':>5}  name")
    for o in sorted(set(seen) | {int(k) for k in names}):
        n = names.get(str(o))
        print(f"  {o:>9}  {seen.get(o, 0):>5}  "
              f"{n if n else '(unnamed - fh6_cars.py ' + str(o) + ' \"...\")'}")
    print()


if __name__ == "__main__":
    main()
