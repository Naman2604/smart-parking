"use client";

import { useState } from "react";
import { useTheme } from "next-themes";
import { Moon, RefreshCw, Sun, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useBackendHealth } from "@/hooks/use-health";
import { REFRESH_EVENT } from "@/hooks/use-polling";

export function FooterControls() {
  const { theme, setTheme } = useTheme();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const { health, reachable, running, connecting } = useBackendHealth();

  // Ask every data poller on the page to fetch again right now
  const handleRefresh = () => {
    setIsRefreshing(true);
    window.dispatchEvent(new Event(REFRESH_EVENT));
    setTimeout(() => setIsRefreshing(false), 1000);
  };

  const status = running
    ? { label: "All Systems Operational", pill: "bg-primary/10", dot: "bg-primary", text: "text-primary" }
    : connecting
      ? { label: "Checking…", pill: "bg-amber-500/10", dot: "bg-amber-500", text: "text-amber-500" }
      : reachable
        ? { label: `Starting up (${health?.stage ?? "…"})`, pill: "bg-amber-500/10", dot: "bg-amber-500", text: "text-amber-500" }
        : { label: "Backend Unreachable", pill: "bg-destructive/10", dot: "bg-destructive", text: "text-destructive" };

  return (
    <footer className="flex flex-col items-center justify-between gap-4 border-t border-border bg-card px-6 py-4 sm:flex-row">
      <div className="flex items-center gap-2">
        <Shield className="h-3.5 w-3.5 text-primary" />
        <span className="text-xs text-muted-foreground">
          System Health:
        </span>
        <span className={`flex items-center gap-1.5 rounded-full px-2.5 py-0.5 ${status.pill}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
          <span className={`text-[10px] font-semibold ${status.text}`}>{status.label}</span>
        </span>
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handleRefresh}
          disabled={isRefreshing}
          className="h-8 gap-1.5 border-border bg-secondary text-xs text-foreground hover:bg-muted"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${isRefreshing ? "animate-spin" : ""}`}
          />
          {isRefreshing ? "Refreshing..." : "Refresh Data"}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="h-8 w-8 border-border bg-secondary p-0 text-foreground hover:bg-muted"
          aria-label="Toggle theme"
        >
          <Sun className="h-3.5 w-3.5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
          <Moon className="absolute h-3.5 w-3.5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
        </Button>
      </div>
    </footer>
  );
}
