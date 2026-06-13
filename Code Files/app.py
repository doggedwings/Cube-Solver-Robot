import cv2
import numpy as np
import kociemba
import threading
import time
import base64
import serial
import serial.tools.list_ports
from flask import Flask, Response, jsonify, render_template_string, request

app = Flask(__name__)

FACE_LABELS = {
    "U": "White / Up",
    "R": "Red / Right",
    "F": "Green / Front",
    "D": "Yellow / Down",
    "L": "Orange / Left",
    "B": "Blue / Back",
}

FACE_CSS = {
    "U": "#ffffff",
    "R": "#c41e3a",
    "F": "#009b48",
    "D": "#ffd500",
    "L": "#ff5800",
    "B": "#0046ad",
}

SERIAL_PORT = "COM3"
SERIAL_BAUD = 115200
serial_conn = None

def connect_serial():
    global serial_conn
    try:
        serial_conn = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=2)
        time.sleep(2)
        print(f"Serial connected on {SERIAL_PORT}")
    except Exception as e:
        print(f"Serial not connected: {e}")
        serial_conn = None

def send_solution_serial(solution_str):
    if serial_conn and serial_conn.is_open:
        serial_conn.write((solution_str + '\n').encode())
        print(f"Sent to ESP32: {solution_str}")
        return True
    print("Serial not connected")
    return False

state = {
    "refs": {},
    "faces": {f: None for f in "URFDLB"},
    "calibrating": True,
    "calib_face": "U",
    "solution": None,
    "error": None,
    "last_frame": None,  # base64 jpeg from phone
}
state_lock = threading.Lock()

# ── Colour helpers ────────────────────────────────────────────
def hue_dist(h1, h2):
    d = abs(int(h1) - int(h2))
    return min(d, 180 - d)

def color_dist(hsv1, ref):
    h1, s1, v1 = hsv1
    h2, s2, v2 = ref
    dh = hue_dist(h1, h2) * 2
    return dh * dh + (int(s1) - int(s2)) ** 2 + (int(v1) - int(v2)) ** 2

def build_thresholds(refs):
    """Adaptive thresholds anchored by the 6 calibration samples."""
    white_s = refs["U"][1]
    colored_sats = [refs[f][1] for f in "RFDLB"]
    min_colored_s = min(colored_sats)
    return {
        "white_s_max": (white_s + min_colored_s) / 2.0,
        "hues": {
            "R": refs["R"][0],
            "L": refs["L"][0],
            "D": refs["D"][0],
            "F": refs["F"][0],
            "B": refs["B"][0],
        },
    }

def classify_color(hsv, refs):
    """Calibrated hue-distance classifier.

    1. White detected by LOW SATURATION — cutoff sits halfway between the
       calibrated white saturation and the least-saturated colored face,
       so it adapts to your lighting instead of using a fixed number.
    2. All other stickers classified by NEAREST CALIBRATED HUE using
       circular hue distance — correctly handles red wrapping around
       0/180 in OpenCV HSV space, and separates red from orange based on
       the actual calibrated hues of YOUR cube under YOUR lighting.
    Value (brightness) is deliberately ignored for colors — brightness
    varies with shadows/angle but hue stays stable, which is what makes
    this approach accurate.
    """
    if len(refs) < 6:
        return "?"
    h, s, v = hsv
    t = build_thresholds(refs)
    if s <= t["white_s_max"] and v > 90:
        return "U"
    best, best_d = "U", 999
    for face, ref_h in t["hues"].items():
        d = hue_dist(h, ref_h)
        if d < best_d:
            best_d, best = d, face
    return best

def decode_frame(b64_data):
    """Decode base64 image from phone camera."""
    try:
        if ',' in b64_data:
            b64_data = b64_data.split(',')[1]
        img_bytes = base64.b64decode(b64_data)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        return frame
    except Exception as e:
        print(f"Frame decode error: {e}")
        return None

def draw_calib_overlay(frame, face):
    fh, fw = frame.shape[:2]
    # Calibration box matches the size of ONE cell of the 3x3 scan grid
    side = int(min(fh, fw) * 0.85)
    grid_size = side // 3 - 6
    sz = grid_size // 2
    cx, cy = fw // 2, fh // 2
    x1, y1, x2, y2 = cx - sz, cy - sz, cx + sz, cy + sz
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 3)
    cv2.putText(frame, f"Calibrate: {FACE_LABELS[face]}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
    hsv_f = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    roi = hsv_f[y1:y2, x1:x2]
    pad = max(1, sz // 2)
    if roi.shape[0] > 2 * pad and roi.shape[1] > 2 * pad:
        inner = roi[pad:-pad, pad:-pad]
    else:
        inner = roi
    ah = int(np.median(inner[:, :, 0]))
    as_ = int(np.median(inner[:, :, 1]))
    av = int(np.median(inner[:, :, 2]))
    cv2.putText(frame, f"HSV: {ah},{as_},{av}", (x1, y2 + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 0), 1)
    return (ah, as_, av)

def sample_face(frame, refs):
    h, w = frame.shape[:2]
    side = int(min(h, w) * 0.85)
    grid_size = side // 3 - 6
    spacing = 6
    total = 3 * grid_size + 2 * spacing
    sx = w // 2 - total // 2
    sy = h // 2 - total // 2
    hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    labels = []
    for row in range(3):
        for col in range(3):
            x = sx + col * (grid_size + spacing)
            y = sy + row * (grid_size + spacing)
            cv2.rectangle(frame, (x, y), (x + grid_size, y + grid_size), (0, 255, 0), 2)
            roi = hsv_frame[y:y + grid_size, x:x + grid_size]
            pad = grid_size // 4
            inner = roi[pad:-pad, pad:-pad]
            avg = (int(np.median(inner[:, :, 0])),
                   int(np.median(inner[:, :, 1])),
                   int(np.median(inner[:, :, 2])))
            label = classify_color(avg, refs) if refs else "?"
            labels.append(label)
            cv2.putText(frame, label, (x + 5, y + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    return labels

# ── Routes ────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/frame", methods=["POST"])
def api_frame():
    """Receive frame from phone camera via POST."""
    data = request.get_json()
    if not data or 'frame' not in data:
        return jsonify(error="No frame"), 400
    frame = decode_frame(data['frame'])
    if frame is None:
        return jsonify(error="Bad frame"), 400

    with state_lock:
        calibrating = state["calibrating"]
        calib_face = state["calib_face"]
        refs = dict(state["refs"])

    hsv_reading = None
    labels = None

    if calibrating and calib_face:
        hsv_reading = draw_calib_overlay(frame, calib_face)
        with state_lock:
            state["current_hsv"] = hsv_reading
    elif refs:
        labels = sample_face(frame, refs)

    ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
    if ret:
        b64 = base64.b64encode(jpeg.tobytes()).decode()
        with state_lock:
            state["last_frame"] = b64

    return jsonify(ok=True, hsv=hsv_reading, labels=labels)

@app.route("/api/state")
def api_state():
    with state_lock:
        refs_keys = list(state["refs"].keys())
        faces = {k: v for k, v in state["faces"].items()}
        calibrating = state["calibrating"]
        calib_face = state["calib_face"]
        solution = state["solution"]
        error = state["error"]
        last_frame = state.get("last_frame")
    serial_status = "connected" if serial_conn and serial_conn.is_open else "disconnected"
    return jsonify(refs=refs_keys, faces=faces, calibrating=calibrating,
                   calib_face=calib_face, solution=solution, error=error,
                   serial=serial_status, last_frame=last_frame)

@app.route("/api/calibrate/<face>", methods=["POST"])
def api_calibrate(face):
    face = face.upper()
    if face not in "URFDLB":
        return jsonify(error="Invalid face"), 400
    with state_lock:
        hsv = state.get("current_hsv")
        if hsv is None:
            return jsonify(error="No frame yet — point camera at cube first"), 500
        state["refs"][face] = hsv
        order = list("URFDLB")
        idx = order.index(face)
        if idx + 1 < len(order):
            state["calib_face"] = order[idx + 1]
            state["calibrating"] = True
        else:
            state["calibrating"] = False
            state["calib_face"] = None
    return jsonify(ok=True, face=face, hsv=hsv)

@app.route("/api/capture/<face>", methods=["POST"])
def api_capture(face):
    face = face.upper()
    if face not in "URFDLB":
        return jsonify(error="Invalid face"), 400
    with state_lock:
        refs = dict(state["refs"])
        last_frame_b64 = state.get("last_frame")
    if len(refs) < 6:
        return jsonify(error="Calibrate all 6 faces first"), 400
    if last_frame_b64 is None:
        return jsonify(error="No frame available"), 500
    img_bytes = base64.b64decode(last_frame_b64)
    np_arr = np.frombuffer(img_bytes, np.uint8)
    raw = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    labels = sample_face(raw, refs)
    labels[4] = face
    with state_lock:
        state["faces"][face] = labels
    return jsonify(face=face, labels=labels)

@app.route("/api/solve", methods=["POST"])
def api_solve():
    with state_lock:
        faces = {k: v for k, v in state["faces"].items()}
    if any(v is None for v in faces.values()):
        return jsonify(error="Not all 6 faces captured yet"), 400
    cube_str = "".join("".join(faces[f]) for f in "URFDLB")
    try:
        solution = kociemba.solve(cube_str)
        serial_sent = send_solution_serial(solution)
        with state_lock:
            state["solution"] = solution
            state["error"] = None
        return jsonify(solution=solution, cube_str=cube_str, serial_sent=serial_sent)
    except Exception as exc:
        with state_lock:
            state["solution"] = None
            state["error"] = str(exc)
        return jsonify(error=str(exc)), 400

@app.route("/api/set_cell/<face>/<int:index>/<color>", methods=["POST"])
def api_set_cell(face, index, color):
    face = face.upper()
    color = color.upper()
    if face not in "URFDLB" or color not in "URFDLB":
        return jsonify(error="Invalid"), 400
    if not (0 <= index <= 8):
        return jsonify(error="Invalid index"), 400
    with state_lock:
        if state["faces"][face] is None:
            state["faces"][face] = [face] * 9
        state["faces"][face][index] = color
    return jsonify(ok=True)

@app.route("/api/reset", methods=["POST"])
def api_reset():
    with state_lock:
        state["refs"] = {}
        state["faces"] = {f: None for f in "URFDLB"}
        state["calibrating"] = True
        state["calib_face"] = "U"
        state["solution"] = None
        state["error"] = None
        state["last_frame"] = None
        state["current_hsv"] = None
    return jsonify(ok=True)

@app.route("/api/serial_ports")
def api_serial_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return jsonify(ports=ports)

@app.route("/api/connect_serial/<port>", methods=["POST"])
def api_connect_serial(port):
    global serial_conn, SERIAL_PORT
    SERIAL_PORT = port
    connect_serial()
    connected = serial_conn and serial_conn.is_open
    return jsonify(ok=connected, port=port)

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Cube Solver</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #14142b;
  color: #e0e0e0;
  font-family: 'Segoe UI', system-ui, sans-serif;
  min-height: 100vh;
  padding-bottom: 40px;
}
h1 {
  text-align: center;
  padding: 16px 0 8px;
  font-size: 1.4rem;
  color: #f0c040;
  letter-spacing: 2px;
}
.container { max-width: 520px; margin: 0 auto; padding: 0 16px; }

/* Camera */
#camera-wrap {
  position: relative;
  width: 100%;
  background: #000;
  border-radius: 10px;
  overflow: hidden;
  margin-bottom: 12px;
}
#phone-camera {
  width: 100%;
  display: block;
  border-radius: 10px;
}
#overlay-canvas {
  position: absolute;
  top: 0; left: 0;
  width: 100%; height: 100%;
  pointer-events: none;
}
#preview-img {
  width: 100%;
  display: none;
  border-radius: 10px;
}

.banner {
  background: #1c1c3a;
  border: 1px solid #333;
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 0.88rem;
  margin-bottom: 10px;
}
.banner span { color: #f0c040; font-weight: bold; }

.serial-row {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #1c1c3a;
  border: 1px solid #333;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 10px;
  font-size: 0.82rem;
}
.dot { width: 9px; height: 9px; border-radius: 50%; background: #f44336; flex-shrink: 0; }
.dot.on { background: #4caf50; }
select {
  background: #2a2a4a; color: #eee;
  border: 1px solid #444; border-radius: 4px;
  padding: 3px 6px; flex: 1; font-size: 0.8rem;
}

.section-lbl {
  font-size: 0.7rem; color: #888;
  text-transform: uppercase; letter-spacing: 1px;
  margin-bottom: 6px;
}
.btn-row { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 10px; }
button {
  padding: 8px 12px; border-radius: 6px; border: none;
  cursor: pointer; font-weight: 600; font-size: 0.8rem;
  transition: opacity .15s, transform .1s;
}
button:active { transform: scale(0.97); }
button:disabled { opacity: .35; cursor: not-allowed; }
.btn-calib   { background: #00b4d8; color: #000; }
.btn-calib.done { background: #2e7d32; color: #c8e6c9; }
.btn-capture { background: #388e3c; color: #fff; }
.btn-capture.done { background: #1b5e20; }
.btn-solve   { background: #f0c040; color: #000; font-size: .9rem; padding: 10px 20px; }
.btn-reset   { background: #424242; color: #eee; }
.btn-serial  { background: #7c4dff; color: #fff; font-size: 0.78rem; padding: 5px 10px; }

.result-box {
  border-radius: 8px; padding: 12px 14px; font-size: .88rem; margin-top: 10px;
}
.result-box.ok  { background: #0d3b1f; border: 1px solid #2e7d32; }
.result-box.err { background: #3b0d0d; border: 1px solid #b71c1c; color: #ff8a80; }
.result-box h3  { margin-bottom: 6px; color: #f0c040; }
.moves { font-family: monospace; font-size: 1rem; letter-spacing: 2px; word-break: break-all; }

/* Cube net */
.cube-net {
  display: grid;
  grid-template-columns: repeat(4, auto);
  grid-template-areas:
    ".  U  .  ."
    "L  F  R  B"
    ".  D  .  .";
  gap: 5px;
  justify-content: center;
  margin-bottom: 14px;
}
[data-area="U"] { grid-area: U; }
[data-area="L"] { grid-area: L; }
[data-area="F"] { grid-area: F; }
[data-area="R"] { grid-area: R; }
[data-area="B"] { grid-area: B; }
[data-area="D"] { grid-area: D; }
.face-wrap { display: flex; flex-direction: column; align-items: center; gap: 3px; }
.face-lbl { font-size: .62rem; color: #888; }
.face-grid {
  display: grid;
  grid-template-columns: repeat(3, 28px);
  grid-template-rows: repeat(3, 28px);
  gap: 2px; border-radius: 3px; padding: 2px;
}
.face-grid.captured { outline: 2px solid #4caf50; }
.face-grid.active   { outline: 2px solid #00b4d8; }
.cell {
  width: 28px; height: 28px; border-radius: 3px;
  background: #2a2a4a; border: 1px solid #1a1a2e;
  cursor: pointer;
}
</style>
</head>
<body>
<h1>🟧 Cube Solver</h1>
<div class="container">

  <!-- Camera -->
  <div id="camera-wrap">
    <video id="phone-camera" autoplay playsinline muted></video>
    <img id="overlay-img" style="position:absolute;top:0;left:0;width:100%;height:100%;border-radius:10px;pointer-events:none;display:none;">
  </div>

  <!-- Phase -->
  <div class="banner">Phase: <span id="phase-text">Calibration</span></div>

  <!-- Serial -->
  <div class="serial-row">
    <div class="dot" id="serial-dot"></div>
    <span id="serial-status">Serial: disconnected</span>
    <select id="port-select"></select>
    <button class="btn-serial" onclick="connectSerial()">Connect</button>
  </div>

  <!-- Cube net -->
  <div class="section-lbl">Cube faces</div>
  <div class="cube-net">
    <div class="face-wrap" data-area="U"><div class="face-lbl">U·White</div><div class="face-grid" id="face-U"></div></div>
    <div class="face-wrap" data-area="L"><div class="face-lbl">L·Orange</div><div class="face-grid" id="face-L"></div></div>
    <div class="face-wrap" data-area="F"><div class="face-lbl">F·Green</div><div class="face-grid" id="face-F"></div></div>
    <div class="face-wrap" data-area="R"><div class="face-lbl">R·Red</div><div class="face-grid" id="face-R"></div></div>
    <div class="face-wrap" data-area="B"><div class="face-lbl">B·Blue</div><div class="face-grid" id="face-B"></div></div>
    <div class="face-wrap" data-area="D"><div class="face-lbl">D·Yellow</div><div class="face-grid" id="face-D"></div></div>
  </div>

  <!-- Calibration -->
  <div class="section-lbl">1 · Calibrate — point center sticker at box, tap button</div>
  <div class="btn-row">
    <button class="btn-calib" id="calib-U" onclick="calibrate('U')">U·White</button>
    <button class="btn-calib" id="calib-R" onclick="calibrate('R')">R·Red</button>
    <button class="btn-calib" id="calib-F" onclick="calibrate('F')">F·Green</button>
    <button class="btn-calib" id="calib-D" onclick="calibrate('D')">D·Yellow</button>
    <button class="btn-calib" id="calib-L" onclick="calibrate('L')">L·Orange</button>
    <button class="btn-calib" id="calib-B" onclick="calibrate('B')">B·Blue</button>
  </div>

  <!-- Capture -->
  <div class="section-lbl">2 · Capture each face</div>
  <div class="btn-row">
    <button class="btn-capture" id="cap-U" onclick="capture('U')">U↑</button>
    <button class="btn-capture" id="cap-R" onclick="capture('R')">R→</button>
    <button class="btn-capture" id="cap-F" onclick="capture('F')">F●</button>
    <button class="btn-capture" id="cap-D" onclick="capture('D')">D↓</button>
    <button class="btn-capture" id="cap-L" onclick="capture('L')">L←</button>
    <button class="btn-capture" id="cap-B" onclick="capture('B')">B●</button>
  </div>

  <div class="btn-row">
    <button class="btn-solve" onclick="solve()">🔍 Solve & Send</button>
    <button class="btn-reset" onclick="resetAll()">↺ Reset</button>
  </div>

  <div id="result-area"></div>
</div>

<script>
const FACE_CSS = { U:'#ffffff', R:'#c41e3a', F:'#009b48', D:'#ffd500', L:'#ff5800', B:'#0046ad' };
const COLOR_ORDER = ['U','R','F','D','L','B'];
const faceLabels = {};
let streaming = false;
let sendInterval = null;

// Build face grids
['U','R','F','D','L','B'].forEach(f => {
  const g = document.getElementById('face-' + f);
  faceLabels[f] = null;
  let html = '';
  for (let i = 0; i < 9; i++) html += `<div class="cell" data-face="${f}" data-idx="${i}"></div>`;
  g.innerHTML = html;
  g.querySelectorAll('.cell').forEach(cell => cell.addEventListener('click', () => cycleCell(cell)));
});

// Start phone camera
async function startCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 480 } }
    });
    const video = document.getElementById('phone-camera');
    video.srcObject = stream;
    streaming = true;
    sendInterval = setInterval(sendFrame, 200); // send frame every 200ms
  } catch(e) {
    alert('Camera access denied — please allow camera permission and refresh');
  }
}

// Send frame to server
async function sendFrame() {
  if (!streaming) return;
  const video = document.getElementById('phone-camera');
  if (video.readyState < 2) return;
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0);
  const b64 = canvas.toDataURL('image/jpeg', 0.7);
  try {
    await fetch('/api/frame', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frame: b64 })
    });
  } catch(_) {}
}

function cycleCell(cell) {
  const face = cell.dataset.face;
  const idx = parseInt(cell.dataset.idx);
  if (!faceLabels[face]) return;
  const cur = faceLabels[face][idx];
  const next = COLOR_ORDER[(COLOR_ORDER.indexOf(cur) + 1) % COLOR_ORDER.length];
  faceLabels[face][idx] = next;
  cell.style.background = FACE_CSS[next] || '#333';
  fetch(`/api/set_cell/${face}/${idx}/${next}`, { method: 'POST' });
}

function renderFace(face, labels) {
  faceLabels[face] = [...labels];
  const g = document.getElementById('face-' + face);
  const cells = g.querySelectorAll('.cell');
  labels.forEach((lbl, i) => { cells[i].style.background = FACE_CSS[lbl] || '#333'; cells[i].title = lbl; });
  g.classList.add('captured');
  g.classList.remove('active');
}

function clearFace(face) {
  faceLabels[face] = null;
  const g = document.getElementById('face-' + face);
  g.querySelectorAll('.cell').forEach(c => { c.style.background = ''; c.title = ''; });
  g.classList.remove('captured', 'active');
}

async function calibrate(face) {
  const res = await fetch('/api/calibrate/' + face, { method: 'POST' });
  const data = await res.json();
  if (data.ok) {
    const btn = document.getElementById('calib-' + face);
    btn.classList.add('done');
    if (!btn.textContent.includes('✓')) btn.textContent += ' ✓';
  } else {
    showResult(data.error, 'err');
  }
  await pollState();
}

async function capture(face) {
  const res = await fetch('/api/capture/' + face, { method: 'POST' });
  const data = await res.json();
  if (data.labels) {
    renderFace(face, data.labels);
    document.getElementById('cap-' + face).classList.add('done');
  } else {
    showResult(data.error || 'Capture failed', 'err');
  }
}

async function solve() {
  const res = await fetch('/api/solve', { method: 'POST' });
  const data = await res.json();
  if (data.solution) {
    const sentMsg = data.serial_sent
      ? '<p style="color:#4caf50;margin-top:8px">✅ Sent to robot</p>'
      : '<p style="color:#ff8a80;margin-top:8px">⚠️ Serial not connected</p>';
    showResult(`<h3>Solution</h3><div class="moves">${data.solution}</div>${sentMsg}`, 'ok');
  } else {
    showResult(data.error, 'err');
  }
}

async function connectSerial() {
  const port = document.getElementById('port-select').value;
  if (!port) return;
  const res = await fetch('/api/connect_serial/' + encodeURIComponent(port), { method: 'POST' });
  const data = await res.json();
  updateSerialStatus(data.ok);
}

async function loadPorts() {
  const res = await fetch('/api/serial_ports');
  const data = await res.json();
  const sel = document.getElementById('port-select');
  sel.innerHTML = data.ports.map(p => `<option value="${p}">${p}</option>`).join('');
}

function updateSerialStatus(connected) {
  document.getElementById('serial-dot').classList.toggle('on', connected);
  document.getElementById('serial-status').textContent = connected ? 'Serial: connected' : 'Serial: disconnected';
}

async function resetAll() {
  await fetch('/api/reset', { method: 'POST' });
  ['U','R','F','D','L','B'].forEach(f => {
    clearFace(f);
    document.getElementById('cap-' + f).classList.remove('done');
    const cb = document.getElementById('calib-' + f);
    cb.classList.remove('done');
    cb.textContent = cb.textContent.replace(' ✓', '');
  });
  document.getElementById('result-area').innerHTML = '';
  document.getElementById('phase-text').textContent = 'Calibration';
}

function showResult(html, type) {
  document.getElementById('result-area').innerHTML = `<div class="result-box ${type}">${html}</div>`;
}

async function pollState() {
  try {
    const res = await fetch('/api/state');
    const s = await res.json();
    if (s.calibrating) {
      document.getElementById('phase-text').textContent = 'Calibration — ' + (s.calib_face || '');
    } else {
      const n = Object.values(s.faces).filter(v => v !== null).length;
      document.getElementById('phase-text').textContent = `Scanning (${n}/6 faces)`;
    }
    updateSerialStatus(s.serial === 'connected');
    if (s.last_frame) {
      const img = document.getElementById('overlay-img');
      img.src = 'data:image/jpeg;base64,' + s.last_frame;
      img.style.display = 'block';
    }
    (s.refs || []).forEach(f => {
      const btn = document.getElementById('calib-' + f);
      if (btn && !btn.classList.contains('done')) {
        btn.classList.add('done');
        if (!btn.textContent.includes('✓')) btn.textContent += ' ✓';
      }
    });
    for (const [f, labels] of Object.entries(s.faces)) {
      if (labels) {
        renderFace(f, labels);
        document.getElementById('cap-' + f).classList.add('done');
      }
    }
  } catch(_) {}
}

startCamera();
setInterval(pollState, 250);
loadPorts();
pollState();
</script>
</body>
</html>"""

if __name__ == "__main__":
    connect_serial()
    print("Server running:")
    print("  PC:    http://localhost:3000")
    print("  Phone: http://10.0.0.236:3000  (make sure phone is on same WiFi)")
    app.run(host="0.0.0.0", port=3000, threaded=True, ssl_context='adhoc')