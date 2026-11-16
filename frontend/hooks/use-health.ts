"use client";

import { usePolling } from "@/hooks/use-polling";
import type { Health } from "@/lib/dashboard-data";

/** What the backend's /health says, boiled down to a few flags for the UI. */
export function useBackendHealth() {
  const { data, isOffline } = usePolling<Health>("/health", 5000);
  const reachable = data !== null && !isOffline;
  const running = reachable && data.status === "ok";
  return {
    health: data,
    reachable, // the backend answered recently
    running, // ...and detection is producing frames
    connecting: data === null && !isOffline, // first request still pending
    cameraOnline: running && data.video_open === true,
  };
}
