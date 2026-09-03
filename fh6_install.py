#!/usr/bin/env python3
"""
fh6_install.py - put the right version of every tool in the right place.

    python3 fh6_install.py            install/update from ~/Downloads
    python3 fh6_install.py --check    just report what is installed
    python3 fh6_install.py --from DIR install from somewhere else

Browsers save repeat downloads as name-2.py, name-3.py, so the newest copy of
a tool is rarely the one with the plain name - and running an old fh6_draggy.py
silently produces wrong numbers rather than an error. This picks the newest
version of each file by modification time, installs it under the canonical
name, and then verifies the installed copy actually contains the features it
should.

It also creates the virtual environment and installs the two dependencies if
they are missing, so a fresh machine needs nothing else.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")
DEST = os.path.join(HOME, "fh6")
SRC = os.path.join(HOME, "Downloads")
VENV = os.path.join(DEST, "venv")

def venv_python():
    """Windows puts the interpreter in venv\\Scripts, everyone else in bin."""
    if os.name == "nt":
        return os.path.join(VENV, "Scripts", "python.exe")
    return os.path.join(VENV, "bin", "python3")

# Tools that belong in the folder, and a marker proving the installed copy is
# current. The marker is a phrase from the newest working version - if it is
# missing, an older file has been installed over the top.
TOOLS = {
    "fh6_draggy.py":      ("core: telemetry, run detection, ET",   "sustained acceleration"),
    "fh6_reaction.py":    ("main tool: adds reaction time",        "chirp_latency"),
    "fh6_slip.py":        ("time slip + performance report",       "def chart("),
    "fh6_launch.py":      ("tyre temp / launch rpm analysis",      "BY TYRE TEMPERATURE"),
    "fh6_shift.py":       ("optimal shift points",                 "SHIFTING = 11"),
    "fh6_tune.py":        ("detector tuning, held-out",            "held-out"),
    "fh6_audio_probe.py": ("record audio + telemetry",             "NumCylinders"),
    "fh6_audio_map.py":   ("what the countdown detector heard",    "chime/engine"),
    "phone_mic_test.py":  ("can a phone hear the countdown",       "best run on the beat"),
    "udp_probe.py":       ("is anything arriving on the port",     "bound UDP"),
    "fh6_test.py":        ("regression tests, run after updating",
                           "creeping into the beam"),
    "fh6_web.py":         ("browse runs in a browser",             "localhost only"),
    "fh6_strip.py":       ("find the real finish line",            "no single distance"),
    "fh6_merge.py":       ("combine drivers into a leaderboard",   "one leaderboard"),
    "fh6_verify.py":      ("check times against their telemetry",  "unverifiable"),
    "fh6_cars.py":        ("name your cars, ordinals otherwise",   "ordinal-to-name"),
}

REQUIRED = ("fh6_draggy.py", "fh6_reaction.py")
DEPS = ("sounddevice", "numpy")


def newest_variants(folder):
    """Map canonical name -> newest matching file, ignoring -2 / -3 suffixes."""
    best = {}
    if not os.path.isdir(folder):
        return best
    pat = re.compile(r"^([a-z0-9_]+?)(?:[-_ ]?\(?\d+\)?)?\.py$", re.I)
    for entry in os.listdir(folder):
        m = pat.match(entry)
        if not m:
            continue
        canon = m.group(1) + ".py"
        if canon not in TOOLS:
            continue
        path = os.path.join(folder, entry)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if canon not in best or mtime > best[canon][1]:
            best[canon] = (path, mtime)
    return best


def check(name, path):
    """(status, detail) for one installed tool."""
    if not os.path.exists(path):
        return "missing", ""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return "unreadable", str(e)
    marker = TOOLS[name][1]
    if marker.lower() not in text.lower():
        return "OUTDATED", f"missing '{marker}'"
    try:
        compile(text, path, "exec")
    except SyntaxError as e:
        return "BROKEN", f"line {e.lineno}: {e.msg}"
    return "ok", f"{os.path.getsize(path)//1024} kB"


def ensure_venv():
    py = venv_python()
    if not os.path.exists(py):
        print("  creating virtual environment...")
        r = subprocess.run([sys.executable, "-m", "venv", VENV],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  could not create venv:", r.stderr.strip()[:200])
            return None
    missing = []
    for dep in DEPS:
        r = subprocess.run([py, "-c", f"import {dep}"], capture_output=True)
        if r.returncode != 0:
            missing.append(dep)
    if missing:
        print(f"  installing {', '.join(missing)}...")
        r = subprocess.run([py, "-m", "pip", "install", "-q", *missing],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  pip failed:", (r.stderr or r.stdout).strip()[:300])
            if "portaudio" in (r.stderr or "").lower():
                print("  try: brew install portaudio, then run this again")
    return py


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    ap.add_argument("--from", dest="src", default=SRC, help="where downloads live")
    a = ap.parse_args()

    os.makedirs(DEST, exist_ok=True)

    if not a.check:
        found = newest_variants(a.src)
        if not found:
            print(f"no fh6 tools found in {a.src}")
        else:
            print(f"installing from {a.src}\n")
            for name, (path, _) in sorted(found.items()):
                dst = os.path.join(DEST, name)
                same = (os.path.exists(dst)
                        and open(dst, "rb").read() == open(path, "rb").read())
                if same:
                    print(f"  {name:<22} already current")
                    continue
                shutil.copy2(path, dst)
                src_name = os.path.basename(path)
                extra = f"  (from {src_name})" if src_name != name else ""
                print(f"  {name:<22} updated{extra}")
            print()
        ensure_venv()
        print()

    print(f"installed in {DEST}\n")
    print(f"  {'tool':<22}{'status':<11}{'what it does'}")
    print("  " + "-" * 62)
    problems = []
    for name, (desc, _) in TOOLS.items():
        status, detail = check(name, os.path.join(DEST, name))
        if status != "ok":
            if name in REQUIRED or status in ("OUTDATED", "BROKEN"):
                problems.append((name, status, detail))
        flag = "" if status == "ok" else f" <- {detail}" if detail else ""
        print(f"  {name:<22}{status:<11}{desc}{flag}")

    py = venv_python()
    print()
    if os.path.exists(py):
        ok = []
        for dep in DEPS:
            r = subprocess.run([py, "-c", f"import {dep}"], capture_output=True)
            good = r.returncode == 0
            ok.append(f"{dep} {'ok' if good else 'MISSING'}")
            if not good:
                problems.append((dep, "MISSING",
                                 "run without --check to install it"))
        print("  venv: " + ", ".join(ok))
    else:
        print("  venv: not created")
        problems.append(("venv", "missing",
                         "run without --check to create it"))

    if problems:
        print("\n  PROBLEMS")
        for name, status, detail in problems:
            print(f"    {name}: {status} {detail}")
        print("\n  Re-download those files, then run this again.")
    else:
        print("\n  All good. Check it works, then run:\n")
        if os.path.exists(os.path.join(DEST, "fh6_test.py")):
            t = ("venv\\Scripts\\python fh6_test.py" if os.name == "nt"
                 else "venv/bin/python3 fh6_test.py")
            print(f"    cd {DEST} && {t}")
        run = ("venv\\Scripts\\python fh6_reaction.py" if os.name == "nt"
               else "venv/bin/python3 fh6_reaction.py")
        print(f"    cd {DEST} && {run}")


if __name__ == "__main__":
    main()
