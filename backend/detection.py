"""
Vehicle detection and parking-slot occupancy (YOLO26, run with ONNX Runtime).

How it works
------------
1. YOLO26 finds vehicles in a frame. The model is exported "end-to-end"
   (NMS-free): it directly returns the final boxes, so there is no
   post-processing step to tune. It runs with onnxruntime - no PyTorch needed,
   so it fits comfortably in a 512 MB server.
2. Each vehicle is matched to at most ONE parking slot (best overlap / IoU).
3. A slot flips between free/occupied only after the new state has been seen
   for several detections in a row (debouncing), so the dashboard numbers
   don't flicker when the model misses a car for a single frame.

Slot positions live in `slots.json` (created by `python calibrate_slots.py`).
"""
import json
import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

log = logging.getLogger("parking.detection")

BASE_DIR = Path(__file__).resolve().parent

# ── Settings (override with environment variables if you like) ──────────────
# Two models are bundled. The default (960) is a good balance; the 1280 one spots
# more distant cars but uses roughly 100 MB more memory. To use it, set BOTH:
#   YOLO_MODEL=models/yolo26n-1280.onnx  and  SLOTS_FILE=slots-1280.json
# (the slots must come from the same model, because they are learned from what it sees)
MODEL_PATH = os.getenv("YOLO_MODEL", str(BASE_DIR / "models" / "yolo26n-960.onnx"))
SLOTS_PATH = Path(os.getenv("SLOTS_FILE", str(BASE_DIR / "slots.json")))

# COCO class IDs for vehicles
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# Lower this if you're missing cars, raise it to reduce false positives.
# YOLO26 (NMS-free) reports lower confidence scores than older YOLO versions,
# so ~0.10 is normal for it. If you change this, re-run `python calibrate_slots.py`
# with the same value, because the slots are derived from what the model detects.
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.10"))
# CPU threads used by the model. 1 suits small/free servers (more threads on a tiny
# CPU slice just fight each other). Raise it on a bigger machine.
ORT_THREADS = int(os.getenv("ORT_THREADS", "1"))
# A vehicle "belongs" to a slot when their boxes overlap at least this much (IoU)
SLOT_IOU_THRESHOLD = float(os.getenv("SLOT_IOU_THRESHOLD", "0.4"))
# A slot must show a new state this many detections in a row before it flips.
# At the default 0.3s detection interval, 5 = a change shows up after ~1.5s.
DEBOUNCE_DETECTIONS = int(os.getenv("DEBOUNCE_DETECTIONS", "5"))

COLOR_FREE = (0, 200, 0)       # BGR
COLOR_OCCUPIED = (0, 0, 220)
COLOR_OTHER = (200, 140, 40)


def iou(a, b):
    """Intersection-over-union of two (x1, y1, x2, y2) boxes."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


ZONE_ROWS = ["Back", "Middle", "Front"]      # far from the camera -> close to it
ZONE_COLS = ["Left", "Right"]
ZONE_ORDER = [f"{r} {c}" for r in ZONE_ROWS for c in ZONE_COLS]


def compute_zones(slots):
    """
    Name the area of the lot each slot is in (3 bands front-to-back x left/right,
    split so each band has about the same number of slots). Used for the
    per-area utilization chart. Returns one name per slot.
    """
    if not slots:
        return []
    cx = np.array([(s[0] + s[2]) / 2 for s in slots])
    cy = np.array([(s[1] + s[3]) / 2 for s in slots])
    y_cut1, y_cut2 = np.percentile(cy, [100 / 3, 200 / 3])
    x_cut = np.median(cx)
    names = []
    for x, y in zip(cx, cy):
        row = 0 if y <= y_cut1 else 1 if y <= y_cut2 else 2
        names.append(f"{ZONE_ROWS[row]} {ZONE_COLS[0 if x <= x_cut else 1]}")
    return names


def load_slots(path=SLOTS_PATH):
    """
    Returns (slots, frame_start, frame_end).
    slots is a list of (x1, y1, x2, y2). frame_start/end say which part of the
    video the slots were calibrated for (slots only match that camera view).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        log.error("%s not found - run `python calibrate_slots.py` first. "
                  "Running with 0 slots.", path)
        return [], 0, None

    slots = [(x, y, x + w, y + h) for x, y, w, h in data["slots"]]
    return slots, int(data.get("frame_start", 0)), data.get("frame_end")


class ParkingDetector:
    def __init__(self, load_slot_file=True):
        log.info("Loading model: %s", MODEL_PATH)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = ORT_THREADS
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3          # warnings/errors only
        # Don't let idle model threads busy-wait: on a tiny CPU slice that burns the
        # little time we have and can stall startup.
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                f"Model file missing: {MODEL_PATH}. It should be committed to the repo "
                f"at backend/models/. Check it was pushed to GitHub.")
        t0 = time.monotonic()
        log.info("Creating ONNX session (%d thread(s)) from %s (%.1f MB)...",
                 ORT_THREADS, MODEL_PATH, os.path.getsize(MODEL_PATH) / 1e6)
        self.session = ort.InferenceSession(MODEL_PATH, opts, providers=["CPUExecutionProvider"])
        log.info("ONNX session created in %.1fs", time.monotonic() - t0)
        inp = self.session.get_inputs()[0]
        self._input_name = inp.name
        self._in_h, self._in_w = int(inp.shape[2]), int(inp.shape[3])   # 384 x 640 for the bundled model
        if load_slot_file:
            self.slots, self.frame_start, self.frame_end = load_slots()
        else:                       # used by calibrate_slots.py
            self.slots, self.frame_start, self.frame_end = [], 0, None
        self.zones = compute_zones(self.slots)
        self.zone_order = ZONE_ORDER
        self._unassigned = []
        self.reset()
        log.info("Monitoring %d slots (video frames %s-%s)",
                 len(self.slots), self.frame_start, self.frame_end)

    def reset(self):
        """Forget slot history (call when the video loops back to the start)."""
        self.state = [False] * len(self.slots)
        self._streak = [0] * len(self.slots)
        self._first = True

    def warmup(self):
        """Run one dummy inference so the first real frame isn't slow."""
        dummy = np.zeros((1, 3, self._in_h, self._in_w), dtype=np.float32)
        self.session.run(None, {self._input_name: dummy})

    # ── 1. detect ────────────────────────────────────────────────────────
    def _preprocess(self, frame):
        """Resize with padding ("letterbox") to the model's fixed input size."""
        h, w = frame.shape[:2]
        ratio = min(self._in_h / h, self._in_w / w)
        new_w, new_h = round(w * ratio), round(h * ratio)
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        left = int(round((self._in_w - new_w) / 2 - 0.1))
        top = int(round((self._in_h - new_h) / 2 - 0.1))
        canvas = np.full((self._in_h, self._in_w, 3), 114, dtype=np.uint8)
        canvas[top:top + new_h, left:left + new_w] = resized
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)          # BGR->RGB, HWC->CHW
        blob = np.ascontiguousarray(blob, dtype=np.float32)[None] / 255.0
        return blob, ratio, left, top

    def detect(self, frame):
        """Return a list of (x1, y1, x2, y2, label, confidence) for vehicles."""
        fh, fw = frame.shape[:2]
        blob, ratio, left, top = self._preprocess(frame)
        # End-to-end output: up to 300 rows of [x1, y1, x2, y2, score, class]
        out = self.session.run(None, {self._input_name: blob})[0][0]

        cls_ids = out[:, 5].astype(int)
        keep = (out[:, 4] >= CONFIDENCE_THRESHOLD) & np.isin(cls_ids, list(VEHICLE_CLASSES))
        vehicles = []
        for (x1, y1, x2, y2, score, _), cid in zip(out[keep], cls_ids[keep]):
            vehicles.append((
                float(np.clip((x1 - left) / ratio, 0, fw)),
                float(np.clip((y1 - top) / ratio, 0, fh)),
                float(np.clip((x2 - left) / ratio, 0, fw)),
                float(np.clip((y2 - top) / ratio, 0, fh)),
                VEHICLE_CLASSES[int(cid)], float(score)))
        return vehicles

    # ── 2. assign vehicles to slots ──────────────────────────────────────
    def update(self, vehicles):
        """Match vehicles to slots and return the (debounced) occupied flags."""
        pairs = []
        for vi, v in enumerate(vehicles):
            for si, s in enumerate(self.slots):
                score = iou(v[:4], s)
                if score >= SLOT_IOU_THRESHOLD:
                    pairs.append((score, vi, si))

        raw = [False] * len(self.slots)
        used_vehicles = set()
        for score, vi, si in sorted(pairs, reverse=True):   # best matches first
            if vi in used_vehicles or raw[si]:
                continue
            used_vehicles.add(vi)
            raw[si] = True
        self._unassigned = [v for i, v in enumerate(vehicles) if i not in used_vehicles]

        if self._first:                       # first reading: trust it immediately
            self.state = raw
            self._first = False
            return list(self.state)

        for i, seen in enumerate(raw):
            if seen == self.state[i]:
                self._streak[i] = 0
            else:
                self._streak[i] += 1
                if self._streak[i] >= DEBOUNCE_DETECTIONS:
                    self.state[i] = seen
                    self._streak[i] = 0
        return list(self.state)

    # ── 3. draw ──────────────────────────────────────────────────────────
    def draw(self, frame, states):
        out = frame.copy()
        fh, fw = out.shape[:2]

        # vehicles that aren't in a slot (driving through, far away, ...)
        for x1, y1, x2, y2, _label, _conf in self._unassigned:
            cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), COLOR_OTHER, 1)

        overlay = out.copy()
        for (x1, y1, x2, y2), occupied in zip(self.slots, states):
            color = COLOR_OCCUPIED if occupied else COLOR_FREE
            cv2.rectangle(overlay, (int(x1), int(y1)), (int(x2), int(y2)), color, -1)
        out = cv2.addWeighted(overlay, 0.25, out, 0.75, 0)

        for i, ((x1, y1, x2, y2), occupied) in enumerate(zip(self.slots, states), 1):
            color = COLOR_OCCUPIED if occupied else COLOR_FREE
            cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            cv2.putText(out, f"P{i}", (int(x1) + 3, int(y1) + 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

        total = len(self.slots)
        occ = sum(states)
        rate = (occ / total * 100) if total else 0
        hud = f" Total: {total}   Occupied: {occ}   Free: {total - occ}   Rate: {rate:.0f}%"
        cv2.rectangle(out, (0, 0), (fw, 26), (15, 15, 15), -1)
        cv2.putText(out, hud, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                    (210, 210, 210), 1, cv2.LINE_AA)
        return out
