"""
Thread-safe parking statistics, recorded history and simple insights.

Everything is kept in memory: history is lost when the server restarts
(free hosting restarts/sleeps often), and it starts filling up again.
"""
import os
import threading
import time
from collections import deque

# A history point is recorded at most this often (seconds) ...
HISTORY_INTERVAL = float(os.getenv("HISTORY_INTERVAL", "5"))
# ... and this many points are kept (720 x 5 s = the last hour)
HISTORY_MAX_POINTS = int(os.getenv("HISTORY_MAX_POINTS", "720"))
# Trend needs at least this many points (9 x 5 s = 45 s of data)
MIN_TREND_SAMPLES = 9
# Occupancy must move by more than this many percentage points to be "increasing/decreasing"
TREND_THRESHOLD = 3.0
# Detection counts as stalled if stats haven't been updated for this long (seconds)
STALE_AFTER = 10

_lock = threading.Lock()

_stats = {
    "total_slots": 0,
    "occupied": 0,
    "available": 0,
    "occupancy_rate": 0.0,   # percent, 0-100
    "updated_at": None,      # unix timestamp of the last update
}
_slots = []                  # [{"id": "P1", "zone": "Back Left", "occupied": True}, ...]
_zones = []                  # zone name for each slot, same order as the slots
_zone_order = []             # display order of zones
_zone_stats = []             # [{"zone", "total", "occupied", "utilization"}, ...]
_history = deque(maxlen=HISTORY_MAX_POINTS)   # (timestamp, occupied, total)
_last_sample = 0.0


def set_zones(zone_names, order=None):
    """Tell analytics which area of the lot each slot belongs to."""
    global _zones, _zone_order
    with _lock:
        _zones = list(zone_names)
        _zone_order = list(order) if order else []


def _build_zone_stats(slot_data):
    agg = {}
    for i, occ in enumerate(slot_data):
        zone = _zones[i] if i < len(_zones) else "Whole lot"
        total, occupied = agg.get(zone, (0, 0))
        agg[zone] = (total + 1, occupied + (1 if occ else 0))

    def sort_key(zone):
        return _zone_order.index(zone) if zone in _zone_order else len(_zone_order)

    return [{"zone": z, "total": t, "occupied": o,
             "utilization": round(o / t * 100, 1)}
            for z, (t, o) in sorted(agg.items(), key=lambda kv: sort_key(kv[0]))]


def update_stats(slot_data):
    """slot_data: list of booleans / 0-1 ints, one per parking slot."""
    global _slots, _zone_stats, _last_sample
    total = len(slot_data)
    occupied = sum(1 for s in slot_data if s)
    rate = round(occupied / total * 100, 1) if total else 0.0
    now = time.time()

    with _lock:
        _stats.update(total_slots=total, occupied=occupied, available=total - occupied,
                      occupancy_rate=rate, updated_at=now)
        _slots = [{"id": f"P{i}",
                   "zone": _zones[i - 1] if i - 1 < len(_zones) else "Whole lot",
                   "occupied": bool(s)}
                  for i, s in enumerate(slot_data, 1)]
        _zone_stats = _build_zone_stats(slot_data)
        if total and now - _last_sample >= HISTORY_INTERVAL:
            _history.append((now, occupied, total))
            _last_sample = now


def get_stats():
    with _lock:
        data = dict(_stats)
        data["zones"] = [dict(z) for z in _zone_stats]
    # Deprecated aliases so older frontend builds that still read the old
    # key names keep working. Prefer total_slots / occupancy_rate.
    data["total"] = data["total_slots"]
    data["occupancy"] = data["occupancy_rate"]
    return data


def get_slots():
    with _lock:
        return [dict(s) for s in _slots]


def get_history(points=120):
    """The most recent recorded points, oldest first."""
    with _lock:
        recent = list(_history)[-max(1, points):]
    return [{"t": t, "occupied": o, "available": tot - o,
             "occupancy_rate": round(o / tot * 100, 1) if tot else 0.0}
            for t, o, tot in recent]


def get_insights(now=None):
    """Plain statistics over the recorded history, plus rule-based alerts."""
    now = time.time() if now is None else now
    with _lock:
        hist = list(_history)
        st = dict(_stats)
        zones = [dict(z) for z in _zone_stats]

    n = len(hist)
    rates = [(o / t * 100) if t else 0.0 for _, o, t in hist]
    out = {
        "samples": n,
        "window_seconds": round(hist[-1][0] - hist[0][0]) if n > 1 else 0,
        "peak_at": None, "peak_occupied": None, "peak_total": None,
        "average_occupancy": None,
        "trend": "collecting", "trend_change": None,
        "alerts": [],
    }

    if n:
        peak = max(range(n), key=lambda i: (hist[i][1], -i))   # highest; earliest on ties
        out["peak_at"], out["peak_occupied"], out["peak_total"] = hist[peak]
        out["average_occupancy"] = round(sum(rates) / n, 1)

    if n >= MIN_TREND_SAMPLES:
        third = n // 3
        diff = sum(rates[-third:]) / third - sum(rates[:third]) / third
        out["trend_change"] = round(diff, 1)
        out["trend"] = ("increasing" if diff > TREND_THRESHOLD
                        else "decreasing" if diff < -TREND_THRESHOLD else "stable")

    alerts = out["alerts"]
    total, avail, rate = st["total_slots"], st["available"], st["occupancy_rate"]
    if st["updated_at"] is not None and now - st["updated_at"] > STALE_AFTER:
        alerts.append({"type": "warning", "message": "Detection has stopped updating - the numbers may be out of date"})
    if total:
        if avail == 0:
            alerts.append({"type": "warning", "message": "Parking lot is full - no free spaces"})
        elif rate >= 90:
            alerts.append({"type": "warning",
                           "message": f"Parking lot almost full: {avail} of {total} spaces free ({rate:.0f}% occupied)"})
            # per-area alerts only add information when the lot as a whole isn't already flagged
        for z in sorted(zones, key=lambda z: -z["utilization"])[:2]:
            if avail and rate < 90 and z["total"] >= 3 and z["utilization"] >= 90:
                alerts.append({"type": "warning",
                               "message": f"{z['zone']} is nearly full ({z['utilization']:.0f}% of {z['total']} spaces)"})
        if not alerts:
            alerts.append({"type": "info", "message": f"No active alerts - {avail} of {total} spaces free"})
    elif not alerts:
        alerts.append({"type": "info", "message": "Waiting for detection data"})
    return out
