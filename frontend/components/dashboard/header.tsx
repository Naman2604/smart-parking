"use client";

import { useEffect, useState } from "react";
import { Activity, Camera, Wifi, WifiOff } from "lucide-react";
import { useBackendHealth } from "@/hooks/use-health";

export function DashboardHeader() {
  const [currentTime, setCurrentTime] = useState(new Date());
  const { health, reachable, running, connecting, cameraOnline } = useBackendHealth();

  // One short label + colour for the overall system state
  const system = running
    ? { label: "System Active", dot: "bg-primary", text: "text-primary", ping: true }
    : connecting
      ? { label: "Connecting…", dot: "bg-amber-500", text: "text-amber-500", ping: false }
      : reachable
        ? { label: `Starting (${health?.stage ?? "…"})`, dot: "bg-amber-500", text: "text-amber-500", ping: false }
        : { label: "Backend Offline", dot: "bg-destructive", text: "text-destructive", ping: false };

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <header className="flex flex-col gap-4 border-b border-border bg-card px-6 py-4 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
            <Activity className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-foreground lg:text-xl">
              Smart Parking Control Center
            </h1>
            <p className="text-xs text-muted-foreground">
              Real-time monitoring & analytics
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4 lg:gap-6">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            {system.ping && (
              <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${system.dot}`} />
            )}
            <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${system.dot}`} />
          </span>
          <span className={`text-xs font-medium ${system.text}`}>{system.label}</span>
        </div>

        <div className="flex items-center gap-2 rounded-md bg-secondary px-3 py-1.5">
          <Camera className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{cameraOnline ? 1 : 0}</span>/1 Camera
            Online
          </span>
        </div>

        <div className="flex items-center gap-2 rounded-md bg-secondary px-3 py-1.5">
          {reachable ? (
            <Wifi className="h-3.5 w-3.5 text-primary" />
          ) : (
            <WifiOff className="h-3.5 w-3.5 text-destructive" />
          )}
          <span className="text-xs text-muted-foreground">
            {reachable ? "Connected" : connecting ? "Connecting…" : "Disconnected"}
          </span>
        </div>

        <div className="font-mono text-xs text-muted-foreground">
          <span className="text-foreground">
            {currentTime.toLocaleDateString("en-US", {
              weekday: "short",
              month: "short",
              day: "numeric",
              year: "numeric",
            })}
          </span>
          <span className="mx-2 text-border">|</span>
          <span className="tabular-nums text-foreground">
            {currentTime.toLocaleTimeString("en-US", { hour12: false })}
          </span>
        </div>
      </div>
    </header>
  );
}
