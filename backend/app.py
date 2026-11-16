"""
Flask backend for the Smart Parking system.

A single background thread reads the video, runs detection a few times per
second, updates the stats, and keeps the latest annotated JPEG in memory.
Every browser that opens /video_feed just receives that latest frame, so:
  * stats stay live even when nobody is watching the video,
  * many viewers don't slow each other down or fight over the video file.

Run locally:      python app.py
Run in production: gunicorn app:app --workers 1 --threads 16 --timeout 120
(keep --workers at 1: the detector and its stats live inside one process)
"""
import os

# A small/free server gets a tiny slice of ONE cpu core, but the container often
# still *reports* many cores. Libraries then start one thread per reported core and
# fight over that slice, which can make startup crawl or appear to hang. Keep them
# to one thread. This must run before those libraries are imported. Set the variables
# yourself (e.g. OMP_NUM_THREADS=4) on a bigger machine to override.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import logging
import sys
import threading
import time
import traceback

import cv2
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from analytics import (HISTORY_MAX_POINTS, get_history, get_insights, get_slots,
                       get_stats, set_zones, update_stats)
from detection import BASE_DIR, ParkingDetector

cv2.setNumThreads(int(os.getenv("CV_THREADS", "1")))
START_TIME = time.time()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("parking.app")

VIDEO_PATH = os.getenv("VIDEO_PATH", str(BASE_DIR / "parking.mp4"))
STREAM_FPS = float(os.getenv("STREAM_FPS", "15"))            # frames sent to browsers per second
DETECT_INTERVAL = float(os.getenv("DETECT_INTERVAL", "0.3"))  # seconds between YOLO runs
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "70"))
# If a startup stage takes longer than this, record where the thread is stuck so
# /health can show it (useful when you can't read the server's logs).
STUCK_AFTER = float(os.getenv("STUCK_AFTER", "60"))


def cpu_report():
    """What the container says about CPUs, and the real quota if there is one."""
    info = {"cpus_visible": os.cpu_count()}
    override = os.getenv("CPU_QUOTA_CORES")
    if override:                      # manual override if auto-detection fails
        try:
            info["cpu_quota_cores"] = float(override)
            return info
        except ValueError:
            pass
    try:
        info["cpus_usable"] = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        pass
    for path, parse in (
        ("/sys/fs/cgroup/cpu.max", lambda t: None if t.split()[0] == "max"
         else round(int(t.split()[0]) / int(t.split()[1]), 3)),
        ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", None),
    ):
        try:
            with open(path) as fh:
                text = fh.read().strip()
            if parse:
                info["cpu_quota_cores"] = parse(text)
            break
        except (OSError, ValueError, IndexError, ZeroDivisionError):
            continue
    return info


# Free/small instances often allow only a fraction of one CPU core while reporting
# many. Detecting 3x a second and streaming 15 fps is then far more work than the
# machine can do, so pick gentler settings automatically. Anything set explicitly
# as an environment variable always wins.
CPU_QUOTA = cpu_report().get("cpu_quota_cores")
LOW_CPU = bool(CPU_QUOTA and CPU_QUOTA < 0.5)
if LOW_CPU:
    if "DETECT_INTERVAL" not in os.environ:
        DETECT_INTERVAL = 1.5
    if "STREAM_FPS" not in os.environ:
        STREAM_FPS = 4.0
    if "JPEG_QUALITY" not in os.environ:
        JPEG_QUALITY = 60
    log.info("Small CPU allowance (%.2f cores): using detect every %.1fs, %.0f fps stream.",
             CPU_QUOTA, DETECT_INTERVAL, STREAM_FPS)


class FrameProducer(threading.Thread):
    """Reads the video, runs detection, and publishes the newest JPEG frame."""

    def __init__(self):
        super().__init__(daemon=True, name="frame-producer")
        self._cond = threading.Condition()
        self._jpeg = None
        self._frame_id = 0
        self.last_frame_time = None
        self.video_open = False
        self.last_error = None
        self.detections_run = 0
        self.slot_count = 0
        # starting -> loading_model -> warming_up -> opening_video -> running
        self.stage = "starting"
        self.stage_since = time.time()
        self.stuck_trace = None      # filled in by the watchdog if a stage takes too long
        self.restarts = 0
        self._detector = None

    def _set_stage(self, stage):
        self.stage = stage
        self.stage_since = time.time()
        self.stuck_trace = None
        log.info("stage -> %s (%.1fs after start)", stage, time.time() - START_TIME)

    def stage_seconds(self):
        return time.time() - self.stage_since

    def capture_stack(self):
        """Where is this thread right now? Used when a stage takes too long."""
        frame = sys._current_frames().get(self.ident)
        if frame is None:
            return None
        lines = traceback.format_stack(frame)
        return [ln.strip() for ln in lines[-6:]]

    # ── used by the Flask routes ─────────────────────────────────────────
    def wait_for_frame(self, last_id, timeout=10):
        with self._cond:
            self._cond.wait_for(lambda: self._frame_id != last_id, timeout)
            return self._frame_id, self._jpeg

    def _publish(self, jpeg):
        with self._cond:
            self._jpeg = jpeg
            self._frame_id += 1
            self.last_frame_time = time.time()
            self._cond.notify_all()

    # ── background loop ──────────────────────────────────────────────────
    def run(self):
        while True:       # never let an error silently kill the stats
            try:
                self._run_once()
            except Exception as exc:            # noqa: BLE001
                self.last_error = repr(exc)
                self.video_open = False
                self.restarts += 1
                self._set_stage("error")
                log.exception("Frame producer stopped; restarting in 3s")
                time.sleep(3)

    def _run_once(self):
        if self._detector is None:          # load the model once, not on every retry
            self._set_stage("loading_model")
            t0 = time.monotonic()
            self._detector = ParkingDetector()
            log.info("Model loaded in %.1fs", time.monotonic() - t0)
            self._set_stage("warming_up")
            self._detector.warmup()
            log.info("Model ready in %.1fs", time.monotonic() - t0)
        detector = self._detector
        self.slot_count = len(detector.slots)
        set_zones(detector.zones, detector.zone_order)
        self._set_stage("opening_video")
        cap = cv2.VideoCapture(VIDEO_PATH)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {VIDEO_PATH}")
        self.video_open = True
        self._set_stage("running")
        self.last_error = None

        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(src_fps / STREAM_FPS))   # source frames per streamed frame
        period = 1.0 / STREAM_FPS
        start = detector.frame_start
        end = detector.frame_end or int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        pos, just_wrapped = start, True
        states = [False] * len(detector.slots)
        last_detect = 0.0
        next_tick = time.monotonic()

        while True:
            ok, frame = cap.read() if pos < end else (False, None)
            if not ok:
                if just_wrapped:
                    raise RuntimeError("Could not read any frames from the video")
                # Reached the end of the calibrated range: loop back to the start
                pos, just_wrapped, last_detect = start, True, 0.0
                cap.set(cv2.CAP_PROP_POS_FRAMES, start)
                detector.reset()
                continue
            just_wrapped = False
            pos += 1

            if time.monotonic() - last_detect >= DETECT_INTERVAL:
                vehicles = detector.detect(frame)
                states = detector.update(vehicles)
                update_stats(states)
                self.detections_run += 1
                last_detect = time.monotonic()

            annotated = detector.draw(frame, states)
            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                self._publish(buf.tobytes())

            # Skip source frames so the video plays at normal speed
            for _ in range(step - 1):
                cap.grab()
                pos += 1

            # Pace the loop; if we're running behind, drop frames instead of slowing down
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif -delay > period:
                for _ in range(int(-delay / period) * step):
                    cap.grab()
                    pos += 1
                next_tick = time.monotonic()


app = Flask(__name__)
CORS(app)

def _watchdog(prod):
    """
    If startup stalls, record where the worker thread is stuck so it shows up in
    /health. Free hosting often gives no log access, so the page has to explain itself.
    """
    while True:
        time.sleep(5)
        if prod.stage not in ("running", "error") and prod.stage_seconds() > STUCK_AFTER:
            if prod.stuck_trace is None:
                prod.stuck_trace = prod.capture_stack() or ["(stack unavailable)"]
                log.warning("Still in stage '%s' after %.0fs. Thread is here:\n%s",
                            prod.stage, prod.stage_seconds(), "\n".join(prod.stuck_trace))


# The worker threads are started lazily, NOT at import time.
#
# Why: some servers import this module and only then fork the process that serves
# requests (gunicorn --preload, and some hosts' defaults). Threads do not survive a
# fork - they keep running in the parent, while the child that answers requests has
# none. The symptom is an app frozen at the first stage forever, with no error.
# Starting on the first request means the threads always live in the process that
# serves it, and a thread that dies for any reason is replaced.
_producer = None
_producer_pid = None
_start_lock = threading.Lock()


def get_producer():
    """The frame producer belonging to THIS process, starting it if needed."""
    global _producer, _producer_pid
    pid = os.getpid()
    if _producer is not None and _producer_pid == pid and _producer.is_alive():
        return _producer
    with _start_lock:
        if _producer is None or _producer_pid != pid or not _producer.is_alive():
            if _producer is not None:
                log.warning("Worker thread missing in pid %s (fork or crash) - starting a new one", pid)
            _producer = FrameProducer()
            _producer.start()
            _producer_pid = pid
            threading.Thread(target=_watchdog, args=(_producer,), daemon=True,
                             name="watchdog").start()
            log.info("Worker threads started in pid %s. CPU: %s", pid, cpu_report())
    return _producer


@app.before_request
def _ensure_running():
    get_producer()


@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def mjpeg_stream():
    producer = get_producer()
    last_id = -1
    while True:
        frame_id, jpeg = producer.wait_for_frame(last_id)
        if jpeg is None or frame_id == last_id:     # still starting up / timed out
            continue
        last_id = frame_id
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")


@app.route("/")
def index():
    return jsonify(name="Smart Parking API",
                   endpoints=["/video_feed", "/api/stats", "/api/slots",
                              "/api/history", "/api/insights", "/health"])


@app.route("/video_feed")
def video_feed():
    return Response(mjpeg_stream(),
                    mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"X-Accel-Buffering": "no"})


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/slots")
def api_slots():
    return jsonify(get_slots())


@app.route("/api/history")
def api_history():
    """Recorded occupancy over time. ?points=N returns the latest N points (default 120)."""
    points = request.args.get("points", default=120, type=int)
    return jsonify(get_history(max(1, min(points, HISTORY_MAX_POINTS))))


@app.route("/api/insights")
def api_insights():
    return jsonify(get_insights())


@app.route("/health")
def health():
    """Always 200 so hosting health checks don't kill the app while the model loads."""
    producer = get_producer()
    age = None if producer.last_frame_time is None else round(time.time() - producer.last_frame_time, 1)
    ok = producer.is_alive() and producer.video_open and age is not None and age < 10
    return jsonify(status="ok" if ok else "starting_or_degraded",
                   stage=producer.stage,
                   stage_seconds=round(producer.stage_seconds(), 1),
                   uptime_seconds=round(time.time() - START_TIME, 1),
                   stuck=producer.stuck_trace is not None,
                   stuck_trace=producer.stuck_trace,
                   restarts=producer.restarts,
                   cpu=cpu_report(),
                   low_cpu_mode=LOW_CPU,
                   detect_interval=DETECT_INTERVAL,
                   stream_fps=STREAM_FPS,
                   video_open=producer.video_open,
                   worker_pid=os.getpid(),
                   thread_alive=producer.is_alive(),
                   seconds_since_last_frame=age,
                   detections_run=producer.detections_run,
                   slots_loaded=producer.slot_count,
                   last_error=producer.last_error)


if __name__ == "__main__":
    # debug=False / no reloader: the reloader would start a second video thread
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False, threaded=True)
