#!/usr/bin/env python3
"""
fh6_audio_probe.py - record microphone audio time-aligned to FH6 telemetry.

Purpose: capture the countdown chime and the finish chime alongside the exact
telemetry so their acoustic signatures can be identified once. After that the
main tool can detect them live and there is no more calibration.

The whole point is that both streams are stamped on ONE clock (this Mac's
monotonic clock) as they arrive. Nothing has to be matched up afterwards.

    pip3 install sounddevice numpy

    python3 fh6_audio_probe.py

Do 3 or 4 normal staged runs, then Ctrl-C. It writes:

    fh6_audio.wav    mono 44.1 kHz microphone recording
    fh6_sync.csv     telemetry, each row stamped with the same clock as the audio

Put the Mac somewhere it can hear the TV. Volume at a normal level is fine -
the chime is tonal and stands out from engine noise. Headphones will not work,
the mic has to hear the actual countdown.
"""

import csv
import queue
import socket
import struct
import sys
import time
import wave

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    sys.exit("needs: pip3 install sounddevice numpy")

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from fh6_draggy import parse, _SIZE           # reuse the verified packet parser

import argparse as _argparse
_ap = _argparse.ArgumentParser(description="record audio + telemetry together")
_ap.add_argument("port", nargs="?", type=int, default=5606)
_ap.add_argument("--loopback", action="store_true",
                 help="Windows: record the game's own audio, not a microphone")
_ap.add_argument("--device", help="input device name or number")
_ap.add_argument("--list-devices", action="store_true")
_A = _ap.parse_args()
if _A.list_devices:
    print(sd.query_devices())
    raise SystemExit(0)
PORT = _A.port
RATE = 44100

# Timestamped, so sessions accumulate instead of overwriting each other.
# This matters more than it looks: tuning the detector on the only recording
# you have proves nothing. Held-out sessions are what separate settings that
# work from settings that merely fit.
_STAMP = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M")
WAV_PATH = f"fh6_{_STAMP}.wav"
CSV_PATH = f"fh6_{_STAMP}.csv"

# CarOrdinal and NumCylinders travel with the session so the analysis can tell
# which car a recording came from, and can work out where that engine's
# harmonics fall relative to the 5200 Hz chime. A four-cylinder puts its
# harmonics twice as close together as a V8, which changes the odds of one
# landing on the tone.
COLS = ["mac_t", "TimestampMS", "IsRaceOn", "CurrentRaceTime", "Speed",
        "Accel", "Brake", "Clutch", "HandBrake", "Gear", "CurrentEngineRpm",
        "CarOrdinal", "NumCylinders", "CarPerformanceIndex"]

audio_q = queue.Queue()
t0 = None


def on_audio(indata, frames, time_info, status):
    """Called by PortAudio. Stamp each block with the shared clock."""
    if status:
        print(f"  audio: {status}", file=sys.stderr)
    audio_q.put((time.monotonic(), indata.copy()))


def main():
    global t0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", PORT))
    sock.settimeout(0.05)

    wav = wave.open(WAV_PATH, "wb")
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(RATE)

    csvf = open(CSV_PATH, "w", newline="")
    w = csv.DictWriter(csvf, fieldnames=COLS, extrasaction="ignore")
    w.writeheader()

    print(f"mic + telemetry (UDP {PORT})")
    print(f"  audio -> {WAV_PATH}")
    print(f"  sync  -> {CSV_PATH}")
    print("\nDo 3-4 normal staged runs. Ctrl-C when done.\n")

    extra = {}
    if _A.device is not None:
        extra["device"] = _A.device
    if _A.loopback:
        if os.name != "nt":
            sys.exit("--loopback is Windows only")
        try:
            extra["extra_settings"] = sd.WasapiSettings(loopback=True)
        except (AttributeError, TypeError):
            sys.exit("this sounddevice build has no WASAPI loopback; "
                     "run: pip install -U sounddevice")
        extra.setdefault("device", sd.default.device[1])
    chans = 2 if "extra_settings" in extra else 1
    stream = sd.InputStream(samplerate=RATE, channels=chans, dtype="float32",
                            blocksize=1024, callback=on_audio, **extra)
    t0 = time.monotonic()
    npkt = 0
    nblk = 0
    peak = 0.0
    last_report = time.monotonic()

    with stream:
        try:
            while True:
                # drain audio blocks
                while True:
                    try:
                        _, block = audio_q.get_nowait()
                    except queue.Empty:
                        break
                    nblk += 1
                    peak = max(peak, float(np.abs(block).max()))
                    wav.writeframes((np.clip(block[:, 0], -1, 1)
                                     * 32767).astype("<i2").tobytes())

                # drain telemetry
                try:
                    buf, _ = sock.recvfrom(2048)
                    p = parse(buf)
                    if p is not None:
                        npkt += 1
                        row = {k: p.get(k) for k in COLS}
                        row["mac_t"] = round(time.monotonic() - t0, 5)
                        w.writerow(row)
                except socket.timeout:
                    pass

                now = time.monotonic()
                if now - last_report > 5:
                    secs = nblk * 1024 / RATE
                    bar = "#" * int(min(peak, 1.0) * 30)
                    print(f"  {secs:6.1f}s audio | {npkt:6d} packets | "
                          f"mic peak {peak:.3f} |{bar}")
                    if peak < 0.005:
                        print("    ^ mic is nearly silent - check macOS mic "
                              "permission and that the TV is audible")
                    peak = 0.0
                    last_report = now
        except KeyboardInterrupt:
            print("\nstopping...")

    wav.close()
    csvf.close()
    print(f"wrote {WAV_PATH} ({nblk * 1024 / RATE:.1f}s) and {CSV_PATH} "
          f"({npkt} packets)")
    print("send me both files.")


if __name__ == "__main__":
    main()
