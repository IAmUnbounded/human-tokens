"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type AppStat = {
  key: string;
  app_name: string;
  display_name: string;
  window_title: string;
  source_host: string;
  category: string;
  totalSeconds: number;
  keystrokes: number;
  outputTokens: number;
  inputTokens: number;
  textInputTokens: number;
  rateInputTokens: number;
};

type HourlyStat = { hour: number; outputTokens: number; inputTokens: number };

type CategoryStat = {
  category: string;
  totalSeconds: number;
  inputTokens: number;
  outputTokens: number;
};

type CaptureStat = {
  captured_at: number;
  app_name: string;
  window_title: string;
  source_host: string;
  input_tokens: number;
  text_excerpt: string;
  status: string;
};

type Stats = {
  outputTokens: number;
  inputTokens: number;
  textInputTokens: number;
  rateInputTokens: number;
  totalSeconds: number;
  productiveSeconds: number;
  consumingSeconds: number;
  keystrokesTotal: number;
  apps: AppStat[];
  categories: CategoryStat[];
  hourly: HourlyStat[];
  captures: CaptureStat[];
  captureHealth: {
    captureCount: number;
    capturedTextTokens: number;
    rateInputTokens: number;
    lastCaptureStatus: string;
    statusCounts: Record<string, number>;
  };
  currentApp: {
    app_name: string;
    display_name: string;
    window_title: string;
    category: string;
    source_host: string;
    seconds: number;
  } | null;
  dbExists: boolean;
};

type TrackerState = {
  running: boolean;
  mode: "launchagent" | "pidfile" | "stopped";
  pid: number | null;
  detail: string;
};

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(Math.round(n));
}

function fmtTime(s: number) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtHour(h: number) {
  if (h === 0) return "12am";
  if (h < 12) return `${h}am`;
  if (h === 12) return "12pm";
  return `${h - 12}pm`;
}

function fmtClock(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });
}

const CATEGORY_COLOR: Record<string, string> = {
  creating: "#2fbf71",
  reading: "#3aa7ff",
  ai: "#a78bfa",
  video: "#e25545",
  social: "#d94fb8",
  communication: "#f2a93b",
  other: "#9ca3af",
  idle: "#6b7280",
};

const CATEGORY_LABEL: Record<string, string> = {
  creating: "Creating",
  reading: "Reading",
  ai: "AI Chat",
  video: "Video",
  social: "Social",
  communication: "Comms",
  other: "Other",
  idle: "Idle",
};

const CAPTURE_STATUS_LABEL: Record<string, string> = {
  "text-ok": "Text captured",
  "meta-ok": "Metadata only",
  "meta-only": "Metadata only",
  "javascript-events-disabled+meta-only": "Enable JavaScript from Apple Events",
  "javascript-blocked+meta-only": "JavaScript capture blocked",
  "automation-blocked": "Automation permission blocked",
  "automation-blocked+meta-only": "Automation permission blocked",
  "capture-failed": "Capture failed",
  "capture-failed+meta-only": "Capture failed",
};

function captureStatusLabel(status: string) {
  return CAPTURE_STATUS_LABEL[status] ?? (status || "waiting");
}

function CategoryBadge({ cat }: { cat: string }) {
  const color = CATEGORY_COLOR[cat] ?? "#9ca3af";
  return (
    <span
      className="inline-flex h-6 items-center rounded-md border px-2 text-[11px] font-medium"
      style={{ background: `${color}1f`, color, borderColor: `${color}55` }}
    >
      {CATEGORY_LABEL[cat] ?? cat}
    </span>
  );
}

function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  color: string;
}) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-card p-5">
      <p className="text-[11px] uppercase tracking-[0.18em] text-stone-500">{label}</p>
      <p className="mt-3 text-3xl font-semibold tracking-tight" style={{ color }}>
        {value}
      </p>
      {sub && <p className="mt-2 text-xs text-stone-500">{sub}</p>}
    </div>
  );
}

function TokenBalance({
  outputTokens,
  inputTokens,
}: {
  outputTokens: number;
  inputTokens: number;
}) {
  const total = outputTokens + inputTokens;
  const outputPct = total > 0 ? (outputTokens / total) * 100 : 0;
  const inputPct = total > 0 ? 100 - outputPct : 0;
  const ratio = outputTokens > 0 ? `1:${(inputTokens / outputTokens).toFixed(1)}` : "input only";

  return (
    <section className="rounded-lg border border-surface-border bg-surface-card p-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-[11px] uppercase tracking-[0.18em] text-stone-500">Token Balance</p>
          <p className="mt-2 text-sm text-stone-400">{ratio}</p>
        </div>
        <div className="text-right">
          <p className="text-xs text-output">Output {outputPct.toFixed(0)}%</p>
          <p className="text-xs text-input">Input {inputPct.toFixed(0)}%</p>
        </div>
      </div>
      <div className="mt-5 flex h-4 overflow-hidden rounded-full bg-surface-border">
        <div
          className="transition-all duration-500"
          style={{ width: `${outputPct}%`, background: "linear-gradient(90deg,#219653,#2fbf71)" }}
        />
        <div
          className="transition-all duration-500"
          style={{ width: `${inputPct}%`, background: "linear-gradient(90deg,#2f80ed,#f2a93b)" }}
        />
      </div>
    </section>
  );
}

function LiveIndicator({ app }: { app: NonNullable<Stats["currentApp"]> }) {
  const color = CATEGORY_COLOR[app.category] ?? "#9ca3af";
  const detail =
    app.source_host && app.window_title
      ? `${app.app_name} · ${app.source_host}`
      : app.source_host || app.app_name;

  return (
    <div className="flex min-h-12 items-center gap-3 rounded-lg border border-surface-border bg-surface-card px-4 py-3">
      <span className="relative flex h-2.5 w-2.5">
        <span
          className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-70"
          style={{ background: color }}
        />
        <span className="relative inline-flex h-2.5 w-2.5 rounded-full" style={{ background: color }} />
      </span>
      <span className="text-xs text-stone-500">Live</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-stone-100">{app.display_name}</span>
        <span className="block truncate text-[11px] text-stone-500">{detail}</span>
      </span>
      <CategoryBadge cat={app.category} />
      <span className="shrink-0 text-xs text-stone-500">{fmtTime(app.seconds)}</span>
    </div>
  );
}

function AppRow({ app, maxSeconds }: { app: AppStat; maxSeconds: number }) {
  const pct = maxSeconds > 0 ? (app.totalSeconds / maxSeconds) * 100 : 0;
  const color = CATEGORY_COLOR[app.category] ?? "#9ca3af";
  const dominant = app.outputTokens >= app.inputTokens ? "out" : "in";
  const tokenValue = dominant === "out" ? app.outputTokens : app.inputTokens;
  const detail =
    app.source_host && app.window_title
      ? `${app.app_name} · ${app.source_host}`
      : app.source_host
        ? app.app_name
        : app.window_title || CATEGORY_LABEL[app.category] || app.category;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_70px_76px] items-center gap-x-3 gap-y-2 py-3 max-sm:grid-cols-[minmax(0,1fr)_72px]">
      <div className="min-w-0">
        <p className="truncate text-sm text-stone-200">{app.display_name}</p>
        <p className="truncate text-[11px] text-stone-500">{detail}</p>
      </div>
      <p className="text-right text-xs text-stone-400">{fmtTime(app.totalSeconds)}</p>
      <p className="text-right text-xs" style={{ color: dominant === "out" ? "#2fbf71" : "#3aa7ff" }}>
        {fmt(tokenValue)} {dominant}
      </p>
      <div className="col-span-3 h-2.5 overflow-hidden rounded-full bg-surface-border max-sm:col-span-2">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function CategoryMix({
  categories,
  totalSeconds,
}: {
  categories: Stats["categories"];
  totalSeconds: number;
}) {
  return (
    <section className="rounded-lg border border-surface-border bg-surface-card p-5">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-[11px] uppercase tracking-[0.18em] text-stone-500">Category Mix</p>
        <p className="text-xs text-stone-500">{fmtTime(totalSeconds)} tracked</p>
      </div>
      <div className="flex h-3 overflow-hidden rounded-full bg-surface-border">
        {categories.map((c) => {
          const pct = totalSeconds > 0 ? (c.totalSeconds / totalSeconds) * 100 : 0;
          if (pct <= 0) return null;
          return (
            <div
              key={c.category}
              className="h-full transition-all duration-500"
              style={{ width: `${pct}%`, background: CATEGORY_COLOR[c.category] ?? "#9ca3af" }}
              title={`${CATEGORY_LABEL[c.category] ?? c.category}: ${fmtTime(c.totalSeconds)}`}
            />
          );
        })}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        {categories.map((c) => (
          <div key={c.category} className="flex items-center gap-2">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ background: CATEGORY_COLOR[c.category] ?? "#9ca3af" }}
            />
            <div className="min-w-0">
              <p className="truncate text-xs text-stone-300">
                {CATEGORY_LABEL[c.category] ?? c.category}
              </p>
              <p className="truncate text-[11px] text-stone-500">
                {fmtTime(c.totalSeconds)} · {fmt(c.inputTokens)} in / {fmt(c.outputTokens)} out
              </p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function CaptureRow({ capture }: { capture: CaptureStat }) {
  const source = capture.window_title || capture.source_host || capture.app_name;
  const detail =
    capture.source_host && capture.window_title
      ? `${capture.app_name} · ${capture.source_host}`
      : capture.app_name;
  const status = captureStatusLabel(capture.status);
  return (
    <div className="border-t border-surface-border py-3 first:border-t-0">
      <div className="flex items-center gap-3">
        <p className="w-16 shrink-0 text-xs text-stone-500">{fmtClock(capture.captured_at)}</p>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm text-stone-200">{source}</p>
          <p className="truncate text-[11px] text-stone-500">{detail}</p>
        </div>
        <p
          className="shrink-0 text-right text-xs"
          style={{ color: capture.input_tokens > 0 ? "#3aa7ff" : "#f2a93b" }}
        >
          {capture.input_tokens > 0 ? `${fmt(capture.input_tokens)} in` : status}
        </p>
      </div>
      <p className="mt-1 line-clamp-2 text-xs leading-5 text-stone-500">
        {capture.text_excerpt || status}
      </p>
    </div>
  );
}

function TrackerControl({
  tracker,
  busy,
  onAction,
}: {
  tracker: TrackerState | null;
  busy: boolean;
  onAction: (action: "start" | "stop") => void;
}) {
  const running = tracker?.running ?? false;
  const action = running ? "stop" : "start";
  const label = running ? "Stop" : "Resume";
  const color = running ? "#2fbf71" : "#f2a93b";
  const detail = tracker
    ? tracker.pid
      ? `${tracker.detail} · pid ${tracker.pid}`
      : tracker.detail
    : "Checking tracker";

  return (
    <div className="flex min-h-10 w-full items-center gap-3 rounded-lg border border-surface-border bg-surface-card px-3 py-2 sm:w-auto">
      <span className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
        <span className="text-xs font-medium text-stone-200">
          Tracker {running ? "running" : "stopped"}
        </span>
      </span>
      <span className="min-w-0 flex-1 truncate text-[11px] text-stone-500 sm:max-w-60">{detail}</span>
      <button
        type="button"
        disabled={busy || !tracker}
        onClick={() => onAction(action)}
        className={`h-8 shrink-0 rounded-md border px-3 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
          running
            ? "border-red-900/70 text-red-300 hover:border-red-700 hover:bg-red-950/30"
            : "border-emerald-900/70 text-emerald-300 hover:border-emerald-700 hover:bg-emerald-950/30"
        }`}
      >
        {busy ? "Working" : label}
      </button>
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [tracker, setTracker] = useState<TrackerState | null>(null);
  const [period, setPeriod] = useState<"today" | "24h" | "week">("today");
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [trackerBusy, setTrackerBusy] = useState(false);

  const fetchStats = useCallback(async () => {
    try {
      const response = await fetch(`/api/stats?period=${period}`);
      const data = (await response.json()) as Stats;
      setStats(data);
      setLastUpdate(new Date());
    } catch {
      /* keep the last good frame */
    }
  }, [period]);

  const fetchTracker = useCallback(async () => {
    try {
      const response = await fetch("/api/tracker");
      const data = (await response.json()) as TrackerState;
      setTracker(data);
    } catch {
      setTracker({ running: false, mode: "stopped", pid: null, detail: "Status unavailable" });
    }
  }, []);

  const controlTracker = useCallback(
    async (action: "start" | "stop") => {
      setTrackerBusy(true);
      try {
        const response = await fetch("/api/tracker", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action }),
        });
        const data = (await response.json()) as TrackerState;
        setTracker(data);
        await fetchStats();
      } finally {
        setTrackerBusy(false);
      }
    },
    [fetchStats],
  );

  useEffect(() => {
    fetchStats();
    const id = setInterval(fetchStats, 10_000);
    return () => clearInterval(id);
  }, [fetchStats]);

  useEffect(() => {
    fetchTracker();
    const id = setInterval(fetchTracker, 10_000);
    return () => clearInterval(id);
  }, [fetchTracker]);

  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  const totalInput = stats?.inputTokens ?? 0;
  const visibleTextPct = totalInput > 0 ? ((stats?.textInputTokens ?? 0) / totalInput) * 100 : 0;
  const statusCount = Object.values(stats?.captureHealth.statusCounts ?? {}).reduce(
    (sum, value) => sum + value,
    0,
  );
  const typedOutputEstimate = Math.round((stats?.keystrokesTotal ?? 0) * 0.25);
  const effectiveOutputTokens = Math.max(0, (stats?.outputTokens ?? 0) - typedOutputEstimate);
  const outputSub = stats
    ? effectiveOutputTokens > 0
      ? `${stats.keystrokesTotal.toLocaleString()} keystrokes + ${fmt(effectiveOutputTokens)} effective`
      : `${stats.keystrokesTotal.toLocaleString()} keystrokes`
    : "";
  const outputTrackingLikelyOff =
    !!stats &&
    stats.dbExists &&
    stats.totalSeconds > 1800 &&
    stats.keystrokesTotal === 0 &&
    stats.outputTokens === 0;
  const captureSetupBlocked =
    !!stats &&
    stats.captureHealth.captureCount > 0 &&
    stats.captureHealth.capturedTextTokens === 0 &&
    Object.keys(stats.captureHealth.statusCounts).some((status) =>
      status.includes("javascript-events-disabled") || status.includes("javascript-blocked"),
    );

  return (
    <main className="min-h-screen bg-surface px-4 py-5 text-stone-200 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl">
        <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-stone-50 sm:text-3xl">
              Human Tokens
            </h1>
            <p className="mt-1 text-sm text-stone-500">{today}</p>
          </div>
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:items-end">
            <TrackerControl tracker={tracker} busy={trackerBusy} onAction={controlTracker} />
            <div className="flex w-full gap-2 sm:w-auto">
              {(["today", "24h", "week"] as const).map((item) => (
                <button
                  key={item}
                  onClick={() => setPeriod(item)}
                  className={`h-9 flex-1 rounded-md border px-3 text-xs font-medium transition-colors sm:flex-none ${
                    period === item
                      ? "border-stone-500 bg-stone-700/40 text-stone-100"
                      : "border-surface-border text-stone-500 hover:border-stone-600 hover:text-stone-300"
                  }`}
                >
                  {item === "today" ? "Today" : item === "24h" ? "24 hours" : "7 days"}
                </button>
              ))}
            </div>
          </div>
        </header>

        {stats && !stats.dbExists && (
          <section className="mb-5 rounded-lg border border-amber-700/60 bg-amber-950/20 p-5">
            <p className="text-sm font-medium text-amber-300">No data yet</p>
            <p className="mt-2 text-xs text-amber-200/70">
              <code className="rounded bg-black/30 px-1.5 py-0.5">npm run track:bg</code>
            </p>
          </section>
        )}

        {outputTrackingLikelyOff && (
          <section className="mb-5 rounded-lg border border-amber-700/60 bg-amber-950/20 p-5">
            <p className="text-sm font-medium text-amber-300">Output tracking looks disabled</p>
            <p className="mt-2 text-xs text-amber-200/70">
              Zero keystrokes over {fmtTime(stats?.totalSeconds ?? 0)} of tracked time — the tracker
              likely lacks Accessibility permission. Grant it to your terminal / Python in{" "}
              <code className="rounded bg-black/30 px-1.5 py-0.5">
                System Settings → Privacy &amp; Security → Accessibility
              </code>
              , then restart the tracker.
            </p>
          </section>
        )}

        {captureSetupBlocked && (
          <section className="mb-5 rounded-lg border border-amber-700/60 bg-amber-950/20 p-5">
            <p className="text-sm font-medium text-amber-300">Browser text capture is blocked</p>
            <p className="mt-2 text-xs leading-5 text-amber-200/70">
              Chrome is sharing tab titles and URLs, but not page text. In Chrome, enable{" "}
              <code className="rounded bg-black/30 px-1.5 py-0.5">
                View &gt; Developer &gt; Allow JavaScript from Apple Events
              </code>
              , then restart or wait for the tracker to capture again.
            </p>
          </section>
        )}

        {stats?.currentApp && (
          <div className="mb-5">
            <LiveIndicator app={stats.currentApp} />
          </div>
        )}

        {stats && (
          <>
            <section className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard
                label="Output"
                value={fmt(stats.outputTokens)}
                sub={outputSub}
                color="#2fbf71"
              />
              <StatCard
                label="Input"
                value={fmt(stats.inputTokens)}
                sub={`${fmtTime(stats.consumingSeconds)} consumed`}
                color="#3aa7ff"
              />
              <StatCard
                label="Visible Text"
                value={fmt(stats.textInputTokens)}
                sub={`${visibleTextPct.toFixed(0)}% of input`}
                color="#f2a93b"
              />
              <StatCard
                label="Creating"
                value={fmtTime(stats.productiveSeconds)}
                sub={`${fmtTime(stats.totalSeconds)} tracked`}
                color="#e9e3d5"
              />
            </section>

            <section className="mb-5 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
              <TokenBalance outputTokens={stats.outputTokens} inputTokens={stats.inputTokens} />
              <div className="grid grid-cols-2 gap-3">
                <StatCard
                  label="Media Estimate"
                  value={fmt(stats.rateInputTokens)}
                  sub="time based"
                  color="#e25545"
                />
                <StatCard
                  label="Captures"
                  value={fmt(stats.captureHealth.captureCount)}
                  sub={captureStatusLabel(stats.captureHealth.lastCaptureStatus)}
                  color="#d94fb8"
                />
              </div>
            </section>

            {stats.categories.length > 0 && (
              <section className="mb-5">
                <CategoryMix categories={stats.categories} totalSeconds={stats.totalSeconds} />
              </section>
            )}

            {stats.hourly.length > 0 && (
              <section className="mb-5 rounded-lg border border-surface-border bg-surface-card p-5">
                <div className="mb-4 flex items-center justify-between">
                  <p className="text-[11px] uppercase tracking-[0.18em] text-stone-500">Timeline</p>
                  <p className="text-xs text-stone-500">{period === "today" ? "Today" : "Selected period"}</p>
                </div>
                <ResponsiveContainer width="100%" height={230}>
                  <AreaChart data={stats.hourly.map((h) => ({ ...h, hourLabel: fmtHour(h.hour) }))}>
                    <defs>
                      <linearGradient id="gOut" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#2fbf71" stopOpacity={0.32} />
                        <stop offset="95%" stopColor="#2fbf71" stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="gIn" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#3aa7ff" stopOpacity={0.28} />
                        <stop offset="95%" stopColor="#3aa7ff" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#34342f" />
                    <XAxis
                      dataKey="hourLabel"
                      tick={{ fontSize: 11, fill: "#78716c" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      tick={{ fontSize: 11, fill: "#78716c" }}
                      axisLine={false}
                      tickLine={false}
                      tickFormatter={fmt}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "#181816",
                        border: "1px solid #34342f",
                        borderRadius: 8,
                        color: "#e7e5e4",
                        fontSize: 12,
                      }}
                      formatter={(value: number, name: string) => [
                        fmt(value),
                        name === "outputTokens" ? "Output" : "Input",
                      ]}
                      labelStyle={{ color: "#a8a29e" }}
                    />
                    <Area
                      type="monotone"
                      dataKey="outputTokens"
                      stroke="#2fbf71"
                      fill="url(#gOut)"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Area
                      type="monotone"
                      dataKey="inputTokens"
                      stroke="#3aa7ff"
                      fill="url(#gIn)"
                      strokeWidth={2}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </section>
            )}

            <section className="grid gap-4 lg:grid-cols-[1fr_1fr]">
              <div className="rounded-lg border border-surface-border bg-surface-card p-5">
                <div className="mb-3 flex items-center justify-between">
                  <p className="text-[11px] uppercase tracking-[0.18em] text-stone-500">Apps And Sites</p>
                  <p className="text-xs text-stone-500">{stats.apps.length} sources</p>
                </div>
                <div className="divide-y divide-surface-border">
                  {stats.apps.map((app) => (
                    <AppRow key={app.key} app={app} maxSeconds={stats.apps[0]?.totalSeconds ?? 0} />
                  ))}
                </div>
              </div>

              <div className="rounded-lg border border-surface-border bg-surface-card p-5">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <p className="text-[11px] uppercase tracking-[0.18em] text-stone-500">Text Captured</p>
                  <p className="text-xs text-stone-500">{statusCount} attempts</p>
                </div>
                {stats.captures.length > 0 ? (
                  <div>
                    {stats.captures.map((capture) => (
                      <CaptureRow
                        key={`${capture.captured_at}-${capture.source_host}-${capture.input_tokens}`}
                        capture={capture}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="rounded-md border border-dashed border-surface-border p-4 text-sm text-stone-500">
                    No browser capture attempts in this period.
                  </div>
                )}
              </div>
            </section>
          </>
        )}

        <footer className="mt-6 flex justify-between gap-4 text-[11px] text-stone-600">
          <span>{stats?.captureHealth.lastCaptureStatus || "tracker"}</span>
          {lastUpdate && <span>updated {lastUpdate.toLocaleTimeString()}</span>}
        </footer>
      </div>
    </main>
  );
}
