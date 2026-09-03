#!/usr/bin/env python3
"""
udp_probe.py - is ANYTHING arriving?

Binds a UDP port and prints every datagram it receives with its size and
source, with no filtering or parsing at all. This separates two very
different failures that look identical from the main tool:

  * nothing printed        -> packets are not reaching this machine
                              (wrong IP, firewall, wrong network, not driving)
  * prints, size 324       -> packets arrive fine; the issue is downstream
  * prints, other size     -> something else is sending to this port

Usage:  python3 udp_probe.py [port]      (default 5606)
"""
import socket
import sys
import time

port = int(sys.argv[1]) if len(sys.argv) > 1 else 5606

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", port))
except OSError as e:
    print(f"cannot bind UDP {port}: {e}")
    print("something else is probably already listening on it.")
    sys.exit(1)

print(f"bound UDP 0.0.0.0:{port} on all interfaces")
print("addresses this Mac can be reached at:")
for fam, *_ , addr in socket.getaddrinfo(socket.gethostname(), None):
    if fam == socket.AF_INET:
        print(f"    {addr[0]}")
print("\nwaiting... drive the car in-game (data does not send in menus). Ctrl-C to stop.\n")

s.settimeout(1.0)
n = 0
last = time.time()
while True:
    try:
        buf, src = s.recvfrom(4096)
    except socket.timeout:
        if time.time() - last > 5:
            print("  ... nothing yet")
            last = time.time()
        continue
    except KeyboardInterrupt:
        break
    n += 1
    last = time.time()
    if n <= 5 or n % 60 == 0:
        print(f"#{n:<6} {len(buf):>5} bytes from {src[0]}:{src[1]}")
