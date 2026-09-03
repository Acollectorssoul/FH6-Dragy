#!/usr/bin/env python3
"""
phone_mic_test.py - can your phone hear the FH6 countdown?

Serves a page to your phone that listens through its microphone and reports
every ~5200 Hz tone it detects, with the interval between them. If the
countdown shows up as three beeps about 1.000 s apart, a phone is a viable
capture device and the whole PC-in-the-living-room arrangement goes away.

    python3 phone_mic_test.py

Then open the printed https:// address on your phone, on the same wifi.

Browsers only allow microphone access over https, so this generates a
self-signed certificate. Your phone will warn that the site is not trusted -
that is expected, it is your own laptop. Tap through it.

Nothing is recorded or sent anywhere; detection happens on the phone.
"""

import http.server
import os
import socket
import ssl
import subprocess
import sys
import tempfile

PORT = 8443

PAGE = """<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FH6 countdown listener</title>
<style>
  :root{ --bg:#12150f; --paper:#e4eae0; --ink:#1c2118; --dim:#8d9a86; --hit:#c9d94a; }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--paper);
       font-family:"SF Mono",ui-monospace,Menlo,monospace;
       padding:24px 20px 60px;-webkit-text-size-adjust:100%}
  h1{font-size:15px;letter-spacing:.16em;margin:0 0 4px}
  p.sub{color:var(--dim);font-size:12px;line-height:1.6;margin:0 0 22px}
  button{width:100%;padding:16px;font:inherit;font-size:15px;letter-spacing:.1em;
         background:var(--paper);color:var(--ink);border:0;border-radius:3px}
  .meter{height:8px;background:#232a1e;border-radius:4px;overflow:hidden;margin:22px 0 6px}
  .meter i{display:block;height:100%;width:0;background:var(--dim);transition:width .08s}
  .lvl{font-size:11px;color:var(--dim);letter-spacing:.08em}
  ul{list-style:none;padding:0;margin:20px 0 0;font-size:14px}
  li{display:flex;justify-content:space-between;padding:9px 0;
     border-bottom:1px solid #232a1e;font-variant-numeric:tabular-nums}
  li b{color:var(--hit);font-weight:700}
  li span{color:var(--dim)}
  .gap{color:var(--paper)}
</style></head><body>

<h1>COUNTDOWN LISTENER</h1>
<p class="sub">Put this near the TV and start a drag race. You want
COUNTDOWN to appear, with gaps near 1.000 s. Settings match the tuned
detector, so this is a fair test of what a phone could do.</p>

<button id="go">Start listening</button>

<div class="meter"><i id="bar"></i></div>
<div class="lvl" id="lvl">not listening</div>
<div class="lvl" id="dev" style="margin-top:6px"></div>
<div class="lvl" id="best" style="margin-top:6px">best run on the beat: 0 tones</div>

<ul id="log"></ul>

<script>
const TONE = 5200, HALF = 15, RATIO = 2.0, DEBOUNCE = 0.35;
const GAP = 1.0, TOL = 0.14;             // countdown rhythm, measured
let last = -99, beeps = [];

function log(html){
  const li = document.createElement('li');
  li.innerHTML = html;
  const l = document.getElementById('log');
  l.prepend(li);
  while (l.children.length > 16) l.lastChild.remove();
}

document.getElementById('go').onclick = async () => {
  let stream;
  try {
    // Phone microphones run voice-call processing by default. Noise
    // suppression and automatic gain are built to remove exactly this kind of
    // steady tone, so they must be off. If the browser ignores these, the
    // countdown may be filtered out before we ever see it.
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation:false, noiseSuppression:false,
               autoGainControl:false }
    });
  } catch (e) {
    document.getElementById('lvl').textContent = 'microphone blocked: ' + e.name;
    return;
  }
  document.getElementById('go').style.display = 'none';

  const track = stream.getAudioTracks()[0];
  const st = track.getSettings ? track.getSettings() : {};
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const an = ctx.createAnalyser();
  an.fftSize = 4096; an.smoothingTimeConstant = 0;
  ctx.createMediaStreamSource(stream).connect(an);

  const dsp = [st.noiseSuppression, st.echoCancellation, st.autoGainControl]
              .some(v => v === true);
  document.getElementById('dev').textContent =
      Math.round(ctx.sampleRate) + ' Hz  |  ' +
      (dsp ? 'voice processing STILL ON - may filter the tone'
           : 'voice processing off');

  const bins = new Float32Array(an.frequencyBinCount);
  const hz = ctx.sampleRate / an.fftSize;
  let lo = Math.floor((TONE-HALF)/hz), hi = Math.ceil((TONE+HALF)/hz);
  if (hi < lo) hi = lo;
  const hist = [];

  (function tick(){
    requestAnimationFrame(tick);
    an.getFloatFrequencyData(bins);
    let peak = -Infinity;
    for (let i=lo;i<=hi;i++) if (bins[i] > peak) peak = bins[i];
    if (!isFinite(peak)) return;

    hist.push(peak); if (hist.length > 240) hist.shift();
    const med = [...hist].sort((a,b)=>a-b)[Math.floor(hist.length/2)];
    const over = peak - med;                       // dB above the local floor
    const need = 20*Math.log10(RATIO);             // ~6 dB

    document.getElementById('bar').style.width =
      Math.max(0, Math.min(100, (over/15)*100)) + '%';
    document.getElementById('lvl').textContent =
      over.toFixed(1) + ' dB over floor  (need ' + need.toFixed(1) + ')';

    const t = ctx.currentTime;
    if (over > need && t - last > DEBOUNCE && hist.length > 120) {
      last = t;
      beeps.push(t);
      if (beeps.length > 24) beeps.shift();   // a countdown plus noise
      const gap = beeps.length > 1 ? t - beeps[beeps.length-2] : null;

      // Three tones a second apart is the countdown. Other detections land in
      // between - the game plays a repeating sound about every 0.4 s - so the
      // chain must be allowed to step over them rather than breaking on the
      // first one that does not fit.
      let chain = 1;
      for (let i = 0; i < beeps.length; i++) {
        let n = 1, cur = beeps[i];
        for (let j = i+1; j < beeps.length; j++) {
          if (Math.abs((beeps[j]-cur) - GAP) < TOL) { n++; cur = beeps[j]; }
          else if (beeps[j] - cur > GAP + TOL) break;
        }
        if (n > chain) chain = n;
      }
      log('<b>tone</b><span>' + over.toFixed(1) + ' dB</span>' +
          (gap === null ? '<span>first</span>'
                        : '<span class="gap">+' + gap.toFixed(3) + ' s</span>'));
      document.getElementById('best').textContent =
          'best run on the beat: ' + chain + ' tones';
      if (chain >= 3) log('<b>COUNTDOWN</b><span>' + chain +
                          ' tones on the beat</span>');
    }
  })();
};
</script></body></html>
"""


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    tmp = tempfile.mkdtemp()
    cert, key = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", cert, "-days", "7",
         "-subj", "/CN=fh6"], capture_output=True)
    if r.returncode != 0:
        sys.exit("could not create a certificate (is openssl installed?)\n"
                 + r.stderr.decode()[:400])

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv = http.server.HTTPServer(("0.0.0.0", PORT), H)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

    print(f"\n  On your phone, same wifi, open:\n\n      https://{lan_ip()}:{PORT}\n")
    print("  Your phone will warn the site is untrusted - that is this laptop.")
    print("  Tap Advanced, then continue. Then tap Start listening.\n")
    print("  Ctrl-C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stopped.")


if __name__ == "__main__":
    main()
