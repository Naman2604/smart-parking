// Types for the backend's JSON, plus small pure helpers used by the dashboard.

export interface ZoneStat {
  zone: string;
  total: number;
  occupied: number;
  utilization: number; // percent
}

export interface ParkingStats {
  total_slots?: number;
  total?: number; // older name, still sent by the backend
  occupied?: number;
  available?: number;
  occupancy_rate?: number;
  occupancy?: number; // older name, still sent by the backend
  zones?: ZoneStat[];
}

export interface HistoryPoint {
  t: number; // unix seconds
  occupied: number;
  available: number;
  occupancy_rate: number;
}

export interface Alert {
  type: "warning" | "info";
  message: string;
}

export interface Insights {
  samples: number;
  window_seconds: number;
  peak_at: number | null;
  peak_occupied: number | null;
  peak_total: number | null;
  average_occupancy: number | null;
  trend: "collecting" | "increasing" | "decreasing" | "stable";
  trend_change: number | null;
  alerts: Alert[];
}

export interface Health {
  status: string;
  stage?: string;
  video_open?: boolean;
  seconds_since_last_frame?: number | null;
  last_error?: string | null;
}

/** Normalised numbers from /api/stats (works with old and new key names). */
export function readStats(stats: ParkingStats | null) {
  return {
    total: stats?.total_slots ?? stats?.total ?? 0,
    occupied: stats?.occupied ?? 0,
    available: stats?.available ?? 0,
    rate: stats?.occupancy_rate ?? stats?.occupancy ?? 0,
  };
}

/** "14:32:05" in the viewer's own time zone (the server clock is not used). */
export function clockTime(epochSeconds: number, withSeconds = true): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
    hour12: false,
  });
}

/** Rows for the "usage over time" chart. */
export function toUsageSeries(history: HistoryPoint[]) {
  return history.map((p) => ({
    time: clockTime(p.t),
    occupied: p.occupied,
    available: p.available,
  }));
}

/** 45 -> "45 sec", 180 -> "3 min", 3900 -> "1 h 5 min" */
export function describeWindow(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s} sec`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h} h ${rest} min` : `${h} h`;
}

/** Bar colour for an area's utilization (same thresholds the chart always used). */
export function utilizationColor(percent: number): string {
  if (percent > 80) return "hsl(0, 72%, 51%)";
  if (percent > 60) return "hsl(38, 92%, 50%)";
  return "hsl(152, 70%, 50%)";
}
