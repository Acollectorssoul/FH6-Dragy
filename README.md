# FH6 Draggy

A Draggy-style timing system for Forza Horizon 6. Reaction time, 60 ft,
elapsed times, trap speed, roll-on intervals, track records and leaderboards -
measured from the game's own telemetry, not estimated from video.

Timeslips render in the classic CompuLink two-lane format, results live on a
cork board in your browser, and everything updates live a few seconds after
each pull.

## What it measures

- **Strip runs** - stage, launch on the countdown, and get reaction time,
  60 ft / 330 / 1/8 / 1000 ft / 1/4 (plus 1/2 and full mile on longer strips),
  trap speed, tyre temp at launch, launch rpm and wheelspin.
- **Street pulls** - `--anywhere` measures any standing start on the map, the
  way a real Draggy does. No countdown exists in free roam, so no reaction
  time is claimed.
- **Roll-ons** - pin the throttle from 60 mph and hold it: 60-130, 100-150,
  100-200, 60-200, 150-250 are timed automatically and only count if you stay
  pinned the whole way.
- **Red lights** - leaving before the green voids the run, exactly like a
  real track. Fouls never take a personal best.

Every run saves the telemetry it was derived from, with a hash recorded
alongside the result, so posted times can be re-checked with `fh6_verify.py`
rather than taken on trust.

## Setup

Requires Python 3 (python.org - on Windows, tick "Add Python to PATH").

Download `fh6_setup.py` and run it:

    python3 fh6_setup.py            # Mac
    python  fh6_setup.py            # Windows

It writes every tool into `~/fh6`, creates a virtual environment, installs
the two dependencies (`sounddevice`, `numpy`) and runs the test suite. You
want to see `18/18 passed`.

In FH6: **Settings > HUD and Gameplay > Data Out ON**, IP `127.0.0.1`,
port `5606` (use your PC's IP instead if the game runs on a console).

## Running

    cd ~/fh6
    venv/bin/python3 fh6_reaction.py --driver yourname          # Mac/console setup
    venv\Scripts\python fh6_reaction.py --loopback --driver you # Windows PC

On PC, `--loopback` captures the game's own audio digitally for countdown
detection - far more accurate than a microphone. On console, point the
machine's microphone at the TV; run one short session with engine audio muted
first so the tyre-chirp latency calibration locks in, then play with sound
however you like.

Other modes: `--anywhere` for street pulls, `--strip half` / `--strip mile`
for longer strips, `--raw file.csv` to keep a full packet dump.

## Viewing results

    venv/bin/python3 fh6_web.py

Open http://localhost:8760 - three tabs: **Drag strip** (live board),
**Street** (live board + roll-on bests) and **Results** (history,
leaderboard, track records per class or car). Add `--host 0.0.0.0` to view
from your phone on the same network.

Name your cars once - the game only transmits an ID number:

    venv/bin/python3 fh6_cars.py 3846 "2024 Ford Mustang GT"

## Leaderboards between players

Each player runs with `--driver theirname`, then anyone merges:

    venv/bin/python3 fh6_merge.py leaderboard.json mine.json theirs.json
    venv/bin/python3 fh6_web.py --runs leaderboard.json

`fh6_verify.py leaderboard.json` re-derives every time from its stored
telemetry and flags anything that doesn't add up.

## The tools

| file | what it does |
|---|---|
| `fh6_setup.py` | one-file installer containing everything below |
| `fh6_reaction.py` | the main tool: telemetry + countdown audio |
| `fh6_draggy.py` | core engine: parsing, run detection, ET math |
| `fh6_web.py` | the cork board interface |
| `fh6_slip.py` | CompuLink timeslip + Draggy performance report |
| `fh6_cars.py` | name car ordinals |
| `fh6_merge.py` | combine drivers into a leaderboard |
| `fh6_verify.py` | check times against their saved telemetry |
| `fh6_strip.py` | solve for a strip's true length from a raw dump |
| `fh6_launch.py` | 60 ft vs tyre temp and launch rpm |
| `fh6_shift.py` | optimal shift points from a raw dump |
| `fh6_test.py` | regression tests - run after every update |
| `fh6_tune.py`, `fh6_audio_probe.py`, `fh6_audio_map.py` | detector tuning |
| `phone_mic_test.py`, `udp_probe.py` | diagnostics |

## Honest limitations

- FH6's telemetry has no race-state or green-light marker, so ET is measured
  from first movement. The game's own on-screen time may differ slightly;
  `fh6_strip.py` exists to characterise that.
- Reaction precision is about ±25 ms with digital audio capture (PC), roughly
  ±70 ms through a microphone (console).
- Tyre compound is not in the telemetry; label sessions with `--tyres` if you
  care to track it.
- Verification proves a time follows from real packets; it cannot detect a
  replayed capture.
