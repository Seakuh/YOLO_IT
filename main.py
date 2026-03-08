"""
AI Cam – Multi-Kamera mit Szenen-Switching, Filmschnitt-Szenarien & Aufnahme
Nikon D3100: Via USB (Mini-B Stecker) – mit gphoto2+v4l2loopback als /dev/videoX nutzbar.
"""
import os
import sys
import subprocess

# Qt/OpenCV-Warnungen reduzieren
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.*=false")
import json
import cv2
import random
import time
from datetime import datetime
from pathlib import Path
from ultralytics import YOLO

# Logs reduzieren
os.environ["YOLO_VERBOSE"] = "false"
import logging
logging.getLogger("ultralytics").setLevel(logging.WARNING)

model = YOLO("yolov8n.pt")

# ============ ANSI 90er Farben ============
C = {
    "rst": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "blink": "\033[5m",
    "cyan": "\033[96m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "magenta": "\033[95m",
    "blue": "\033[94m",
}


def format_duration(seconds):
    """Sekunden → MM:SS oder HH:MM:SS."""
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def draw_terminal_ui():
    """90er-Style Terminal Control Panel."""
    ap = "ON " if autopilot else "OFF"
    ap_color = C["green"] + C["bold"] if autopilot else C["red"]
    rec_color = C["red"] + C["bold"] if recording else C["dim"]
    rec_txt = "● REC" if recording else "○ rec"

    elapsed = format_duration(time.time() - session_start_time)
    rec_time = format_duration(time.time() - recording_start_time) if recording and recording_start_time else "00:00"

    lines = [
        "",
        f"  {C['cyan']}╔════════════════════════════════════════════════════╗{C['rst']}",
        f"  {C['cyan']}║{C['rst']}  {C['bold']}{C['magenta']}◆ ▓▒░  AI CAM  CONTROL  PANEL  ░▒▓ ◆{C['rst']}  {C['cyan']}║{C['rst']}",
        f"  {C['cyan']}╠════════════════════════════════════════════════════╣{C['rst']}",
        f"  {C['cyan']}║{C['rst']}  {C['yellow']}AUTOPILOT{C['rst']}  {ap_color}[{ap}]{C['rst']}     {C['dim']}A{C['rst']} = KI an/aus       {C['cyan']}║{C['rst']}",
        f"  {C['cyan']}║{C['rst']}  {rec_color}{rec_txt}{C['rst']}                {C['dim']}R{C['rst']} = Aufnahme       {C['cyan']}║{C['rst']}",
        f"  {C['cyan']}║{C['rst']}  Scene: {C['green']}{active_scene + 1}{C['rst']} ({((name1 if active_scene==0 else name2) or '?')[:14]})  {C['dim']}V{C['rst']}  {C['cyan']}║{C['rst']}",
        f"  {C['cyan']}║{C['rst']}  Cut: {C['blue']}{current_scenario:18}{C['rst']} {C['dim']}S{C['rst']} = Szenario     {C['cyan']}║{C['rst']}",
        f"  {C['cyan']}║{C['rst']}  {C['dim']}8 = Szene 1  2 = Szene 2  SPACE = Cut  ESC = Ende{C['rst']}  {C['cyan']}║{C['rst']}",
        f"  {C['cyan']}║{C['rst']}  Session: {C['blue']}{elapsed}{C['rst']}  │  Rec: {rec_color}{rec_time}{C['rst']}     {C['cyan']}║{C['rst']}",
        f"  {C['cyan']}╚════════════════════════════════════════════════════╝{C['rst']}",
        "",
    ]
    out = "\n".join(lines)
    sys.stdout.write("\033[2J\033[H" + out + "\n")
    sys.stdout.flush()


# ============ Schnitt-Szenarien ============
SCENARIOS = {
    "fixed_3": lambda: 3.0,
    "fixed_5": lambda: 5.0,
    "fixed_7": lambda: 7.0,
    "random_3_7": lambda: random.uniform(3, 7),
    "random_4_10": lambda: random.uniform(4, 10),
    "random_5_15": lambda: random.uniform(5, 15),
    "film": lambda: random.choice([3, 4, 5, 6, 7, 8, 10, 12]),
    "film_varied": lambda: random.choice([3.5, 4.2, 5.7, 6.3, 4.8, 7.2, 5.1, 8.4]),
    "quick_cuts": lambda: random.uniform(1.0, 2.5),
    "action": lambda: random.uniform(0.8, 2.0),
    "long_takes": lambda: random.uniform(10, 20),
    "documentary": lambda: random.choice([5, 8, 12, 15, 20]),
    "talk_show": lambda: random.uniform(8, 15),
}
SCENARIO_NAMES = list(SCENARIOS.keys())
DEFAULT_SCENARIO = "random_4_10"


def open_camera(device):
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(device)
    if cap.isOpened():
        return cap
    try:
        cap.release()
    except Exception:
        pass
    return None


def test_camera(cap):
    return cap and cap.read()[0]


def get_v4l2_device_names():
    """V4L2-Gerätenamen aus v4l2-ctl parsen."""
    try:
        out = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True, text=True, timeout=5
        )
    except Exception:
        return {}
    names = {}
    current = None
    for line in out.stdout.splitlines():
        if "\t" not in line and ":" in line:
            current = line.split(":")[0].strip()
        elif "\t" in line and current:
            dev = line.strip().split()[-1]
            if dev.startswith("/dev/video") and not "media" in dev:
                names[dev] = current
    return names


def discover_cameras():
    """Alle nutzbaren Kameras finden: [(device, name), ...]."""
    v4l_names = get_v4l2_device_names()
    # /dev/video0-9 + bekannte Pfade
    candidates = [f"/dev/video{i}" for i in range(10)]
    found = []
    for dev in candidates:
        c = open_camera(dev)
        if c and test_camera(c):
            name = v4l_names.get(dev, dev)
            found.append((dev, name))
            c.release()
        elif c:
            c.release()
        time.sleep(0.1)
    return found


def load_camera_config():
    """Kamera-Auswahl aus cameras.json oder Umgebungsvariablen."""
    cfg_path = Path(__file__).parent / "cameras.json"
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Env: AI_CAM_CAM1=2 AI_CAM_CAM2=4
    cfg = {}
    if os.environ.get("AI_CAM_CAM1") is not None:
        v = os.environ["AI_CAM_CAM1"]
        cfg["cam1"] = int(v) if v.isdigit() else v
    if os.environ.get("AI_CAM_CAM2") is not None:
        v = os.environ["AI_CAM_CAM2"]
        cfg["cam2"] = int(v) if v.isdigit() else v
    return cfg


# ============ Kameras initialisieren ============
available = discover_cameras()
config = load_camera_config()

cap1, cap2 = None, None
used1, used2 = None, None
name1, name2 = "Cam1", "Cam2"

def _resolve_device(cfg_val):
    """Config-Wert → Gerätepfad."""
    if cfg_val is None:
        return None
    if isinstance(cfg_val, int):
        return f"/dev/video{cfg_val}"
    return str(cfg_val)


def _pick_camera(key, fallback_indices, exclude=None):
    """Kamera aus Config oder Fallback-Reihenfolge öffnen."""
    want = _resolve_device(config.get(key))
    exclude = exclude or ""
    if want:
        order = [want]
    else:
        order = [_resolve_device(i) for i in fallback_indices]
    for dev in order:
        if not dev or dev == exclude:
            continue
        c = open_camera(dev)
        if c and test_camera(c):
            nm = next((n for d, n in available if d == dev), dev)
            return c, dev, nm
        if c:
            c.release()
    return None, None, None


# Cam1
cap1, used1, name1 = _pick_camera("cam1", [2, 0, 4, 1, 3])
if not cap1 and available:
    dev, name1 = available[0]
    cap1 = open_camera(dev)
    used1 = dev

if cap1:
    time.sleep(0.3)
    cap2, used2, name2 = _pick_camera("cam2", [4, 0, 2, 1, 3], exclude=used1)
    if not cap2:
        for dev, nm in available:
            if dev != used1:
                c = open_camera(dev)
                if c and test_camera(c):
                    cap2, used2, name2 = c, dev, nm
                    break
                if c:
                    c.release()

if not cap1:
    err = "Keine Kamera gefunden."
    if not available:
        err += " Nikon D3100? Braucht gphoto2+v4l2loopback → /dev/videoX"
    raise RuntimeError(err)

# Startup-Info
print(f"Cam1: {used1} ({name1})")
if cap2:
    print(f"Cam2: {used2} ({name2})")
else:
    print("Cam2: —")
if available:
    print(f"Verfügbar: {[d for d,_ in available]}")
print("Kamera-Auswahl: cameras.json (cam1, cam2) oder AI_CAM_CAM1/CAM2")

RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(exist_ok=True)

# ============ State ============
writer = None
recording = False
recording_start_time = None
recording_path = None  # aktueller Video-Pfad
recording_metadata = []
recording_frame_count = 0
session_start_time = time.time()
active_scene = 0
next_switch_at = 0.0
current_scenario = DEFAULT_SCENARIO
view_mode = "split" if cap2 else "fullscreen"  # Beide Kameras anzeigen
autopilot = True

ret, frame1 = cap1.read()
if not ret:
    raise RuntimeError("Kann keinen Frame lesen.")
h, w = frame1.shape[:2]
fps = cap1.get(cv2.CAP_PROP_FPS) or 30

pip_w, pip_h = w // 4, h // 4
pip_x, pip_y = w - pip_w - 20, 20


def get_next_switch_interval():
    return SCENARIOS.get(current_scenario, SCENARIOS[DEFAULT_SCENARIO])()


def save_metadata(video_path: Path, duration_sec: float, frame_count: int):
    """Speichert Metadaten als JSON neben dem Video."""
    meta = {
        "video": str(video_path.name),
        "start_time": datetime.fromtimestamp(recording_start_time).isoformat(),
        "duration_seconds": round(duration_sec, 2),
        "frame_count": frame_count,
        "fps": fps,
        "resolution": {"w": w, "h": h},
        "cameras": {"cam1": {"device": str(used1), "name": name1}, "cam2": {"device": str(used2), "name": name2} if cap2 else None},
        "scenario": current_scenario,
        "autopilot": autopilot,
        "view_mode": view_mode,
        "scene_switches": recording_metadata,
    }
    meta_path = video_path.with_suffix(".json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def overlay_transparent(frame, x, y, bw, bh, color, alpha=0.6):
    """Transparenter Overlay-Bereich."""
    fh, fw = frame.shape[:2]
    x2 = min(x + bw, fw)
    y2 = min(y + bh, fh)
    x, y = max(0, x), max(0, y)
    roi = frame[y:y2, x:x2]
    if roi.size == 0:
        return
    overlay = roi.copy()
    cv2.rectangle(overlay, (0, 0), (roi.shape[1], roi.shape[0]), color, -1)
    cv2.addWeighted(overlay, alpha, roi, 1 - alpha, 0, roi)


def draw_record_button(frame, is_recording, x=None, y=None):
    """Roter REC-Button mit weißer Schrift, transparent."""
    fh, fw = frame.shape[:2]
    if x is None:
        x = fw - 120
    if y is None:
        y = 20
    bw, bh = 90, 40
    color = (0, 0, 220) if is_recording else (80, 80, 80)
    overlay_transparent(frame, x, y, bw, bh, color, 0.75)
    cv2.rectangle(frame, (x, y), (x + bw, y + bh), (255, 255, 255), 2)
    txt = "● REC" if is_recording else "○ REC"
    cv2.putText(frame, txt, (x + 12, y + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)


def draw_autopilot_button(frame, x=None, y=None):
    """Großer AUTOPILOT-Button mit schwarzem Hintergrund."""
    fh, fw = frame.shape[:2]
    if x is None:
        x = fw - 120
    if y is None:
        y = 70
    bw, bh = 110, 50
    overlay_transparent(frame, x, y, bw, bh, (0, 0, 0), 0.85)
    cv2.rectangle(frame, (x, y), (x + bw, y + bh), (255, 255, 255), 2)
    status = "ON" if autopilot else "OFF"
    cv2.putText(frame, "AUTOPILOT", (x + 5, y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
    col = (0, 255, 0) if autopilot else (0, 0, 255)
    cv2.putText(frame, f"[{status}]", (x + 25, y + 45), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)


def build_frame():
    ret1, frame1 = cap1.read()
    ret2, frame2 = (cap2.read() if cap2 else (False, None))

    if not ret1:
        return None

    if active_scene == 0:
        main = frame1.copy()
    else:
        main = frame2.copy() if ret2 else frame1.copy()

    results = model.predict(main, verbose=False)
    annotated = results[0].plot()

    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        name = model.names[cls_id]
        cv2.putText(
            annotated, f"{name}: {conf:.0%}",
            (int(box.xyxy[0][0]), int(box.xyxy[0][1]) - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2
        )

    if view_mode == "pip" and cap2 and ret2:
        frame2_small = cv2.resize(frame2, (pip_w, pip_h))
        cv2.rectangle(annotated, (pip_x - 2, pip_y - 2),
                      (pip_x + pip_w + 2, pip_y + pip_h + 2), (255, 255, 255), 2)
        annotated[pip_y:pip_y + pip_h, pip_x:pip_x + pip_w] = frame2_small
        cv2.putText(annotated, "Scene 2" if active_scene == 0 else "Scene 1",
                    (pip_x, pip_y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    elif view_mode == "split" and cap2 and ret2:
        half = w // 2
        left_img = cv2.resize(frame1, (half, h))
        right_img = cv2.resize(frame2, (half, h))
        cv2.putText(left_img, name1[:25], (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.putText(right_img, name2[:25], (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        if active_scene == 0:
            left_ann = cv2.resize(annotated, (half, h))
            annotated = cv2.hconcat([left_ann, right_img])
        else:
            res2 = model.predict(frame2, verbose=False)
            right_ann = cv2.resize(res2[0].plot(), (half, h))
            annotated = cv2.hconcat([left_img, right_ann])
        annotated = cv2.resize(annotated, (w, h))

    label = f"Scene {active_scene + 1}: {name1 if active_scene == 0 else name2}"
    cv2.putText(annotated, label[:50], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(annotated, label[:50], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)

    draw_record_button(annotated, recording)
    draw_autopilot_button(annotated)

    # Zeit-Anzeige
    if recording and recording_start_time:
        rec_str = format_duration(time.time() - recording_start_time)
        cv2.putText(annotated, rec_str, (10, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.putText(annotated, format_duration(time.time() - session_start_time), (10, h - 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
    cv2.putText(annotated, current_scenario, (w - 180, h - 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)

    return annotated


next_switch_at = time.time() + get_next_switch_interval()
last_ui_update = 0

# Erste UI-Ausgabe
draw_terminal_ui()

while True:
    now = time.time()

    # Auto-Switch nur wenn Autopilot AN und 2 Kameras
    if autopilot and cap2 and now >= next_switch_at:
        active_scene = 1 - active_scene
        next_switch_at = now + get_next_switch_interval()
        if recording and recording_start_time:
            recording_metadata.append((round(now - recording_start_time, 2), active_scene + 1))

    frame = build_frame()
    if frame is None:
        break

    if recording and writer is not None:
        writer.write(frame)
        recording_frame_count += 1

    cv2.imshow("AI Cam", frame)
    key = cv2.waitKey(1) & 0xFF

    # Terminal-UI alle 1.5 Sek aktualisieren
    if now - last_ui_update > 1.5:
        draw_terminal_ui()
        last_ui_update = now

    if key == 27:
        break
    elif key == ord("a") or key == ord("A"):
        autopilot = not autopilot
        if autopilot:
            next_switch_at = now + get_next_switch_interval()
        draw_terminal_ui()
    elif key == ord(" ") or key == ord("c") or key == ord("C"):
        if cap2:
            active_scene = 1 - active_scene
            next_switch_at = now + get_next_switch_interval()
            if recording and recording_start_time:
                recording_metadata.append((round(now - recording_start_time, 2), active_scene + 1))
            draw_terminal_ui()
    elif key == ord("1") or key == ord("8"):
        active_scene = 0
        next_switch_at = now + get_next_switch_interval()
        if recording and recording_start_time:
            recording_metadata.append((round(now - recording_start_time, 2), 1))
        draw_terminal_ui()
    elif key == ord("2") and cap2:
        active_scene = 1
        next_switch_at = now + get_next_switch_interval()
        if recording and recording_start_time:
            recording_metadata.append((round(now - recording_start_time, 2), 2))
        draw_terminal_ui()
    elif key == ord("s") or key == ord("S"):
        idx = SCENARIO_NAMES.index(current_scenario) if current_scenario in SCENARIO_NAMES else 0
        idx = (idx + 1) % len(SCENARIO_NAMES)
        current_scenario = SCENARIO_NAMES[idx]
        next_switch_at = now + get_next_switch_interval()
        draw_terminal_ui()
    elif key == ord("v") or key == ord("V"):
        modes = ["fullscreen", "pip", "split"]
        idx = modes.index(view_mode)
        view_mode = modes[(idx + 1) % len(modes)]
        if not cap2:
            view_mode = "fullscreen"
        draw_terminal_ui()
    elif key == ord("r") or key == ord("R"):
        recording = not recording
        if recording:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            recording_path = RECORDINGS_DIR / f"recording_{timestamp}.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(recording_path), fourcc, fps, (w, h))
            recording_start_time = time.time()
            recording_metadata.clear()
            recording_frame_count = 0
        else:
            if writer and recording_path:
                duration = time.time() - recording_start_time
                save_metadata(recording_path, duration, recording_frame_count)
                recording_path = None
            if writer:
                writer.release()
                writer = None
        draw_terminal_ui()

cap1.release()
if cap2:
    cap2.release()
if writer:
    writer.release()
cv2.destroyAllWindows()
