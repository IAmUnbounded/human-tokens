import Database from "better-sqlite3";
import path from "path";

const DB_PATH = path.join(process.cwd(), "..", "data", "sessions.db");

type SessionRow = {
  id: number;
  started_at: number;
  ended_at: number | null;
  app_name: string;
  window_title: string;
  category: string;
  keystrokes: number;
  duration_seconds: number;
  source_url?: string | null;
  source_host?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  text_input_tokens?: number | null;
  rate_input_tokens?: number | null;
  capture_count?: number | null;
  last_capture_status?: string | null;
};

export type AppStat = {
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

export type HourlyStat = {
  hour: number;
  outputTokens: number;
  inputTokens: number;
};

export type CaptureStat = {
  captured_at: number;
  app_name: string;
  window_title: string;
  source_host: string;
  input_tokens: number;
  text_excerpt: string;
  status: string;
};

export type CaptureHealth = {
  captureCount: number;
  capturedTextTokens: number;
  rateInputTokens: number;
  lastCaptureStatus: string;
  statusCounts: Record<string, number>;
};

export type CategoryStat = {
  category: string;
  totalSeconds: number;
  inputTokens: number;
  outputTokens: number;
};

export type StatsResult = {
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
  captureHealth: CaptureHealth;
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

const TOKEN_RATES: Record<string, [number, number]> = {
  creating: [0, 5],
  reading: [0, 200],
  ai: [0, 150],
  video: [0, 180],
  social: [0, 120],
  communication: [0, 75],
  other: [0, 0],
  idle: [0, 0],
};

const CONSUMING_CATEGORIES = new Set([
  "reading",
  "video",
  "social",
  "communication",
  "ai",
]);

function numberValue(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function computeLegacyTokens(category: string, durationSeconds: number, keystrokes: number) {
  const [, inputRate] = TOKEN_RATES[category] ?? [0, 0];
  return {
    outputTokens: Math.round(keystrokes * 0.25),
    inputTokens: Math.round(inputRate * (durationSeconds / 60)),
    textInputTokens: 0,
    rateInputTokens: Math.round(inputRate * (durationSeconds / 60)),
  };
}

function computeSessionTokens(session: SessionRow) {
  const storedSignal =
    numberValue(session.input_tokens) +
    numberValue(session.output_tokens) +
    numberValue(session.text_input_tokens) +
    numberValue(session.rate_input_tokens);

  if (storedSignal > 0) {
    return {
      outputTokens: numberValue(session.output_tokens),
      inputTokens: numberValue(session.input_tokens),
      textInputTokens: numberValue(session.text_input_tokens),
      rateInputTokens: numberValue(session.rate_input_tokens),
    };
  }

  return computeLegacyTokens(
    session.category,
    numberValue(session.duration_seconds),
    numberValue(session.keystrokes),
  );
}

function cleanTitle(title: string | null | undefined) {
  return (title ?? "").replace(/\s+/g, " ").trim();
}

function openDB() {
  try {
    return new Database(DB_PATH, { readonly: true, fileMustExist: true });
  } catch {
    return null;
  }
}

function tableExists(db: Database.Database, table: string) {
  const row = db
    .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name=?")
    .get(table) as { name: string } | undefined;
  return Boolean(row);
}

function tableColumns(db: Database.Database, table: string) {
  if (!tableExists(db, table)) return new Set<string>();
  const rows = db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>;
  return new Set(rows.map((row) => row.name));
}

function emptyStats(dbExists: boolean): StatsResult {
  return {
    outputTokens: 0,
    inputTokens: 0,
    textInputTokens: 0,
    rateInputTokens: 0,
    totalSeconds: 0,
    productiveSeconds: 0,
    consumingSeconds: 0,
    keystrokesTotal: 0,
    apps: [],
    categories: [],
    hourly: [],
    captures: [],
    captureHealth: {
      captureCount: 0,
      capturedTextTokens: 0,
      rateInputTokens: 0,
      lastCaptureStatus: "",
      statusCounts: {},
    },
    currentApp: null,
    dbExists,
  };
}

export function getStats(periodStart: number, periodEnd: number): StatsResult {
  const db = openDB();
  if (!db) return emptyStats(false);

  const sessionColumns = tableColumns(db, "sessions");
  if (!sessionColumns.size) {
    db.close();
    return emptyStats(true);
  }

  const baseColumns = [
    "id",
    "started_at",
    "ended_at",
    "app_name",
    "window_title",
    "category",
    "keystrokes",
    "duration_seconds",
  ];
  const optionalColumns = [
    "source_url",
    "source_host",
    "input_tokens",
    "output_tokens",
    "text_input_tokens",
    "rate_input_tokens",
    "capture_count",
    "last_capture_status",
  ].filter((column) => sessionColumns.has(column));

  const sessions = db
    .prepare(
      `
      SELECT ${[...baseColumns, ...optionalColumns].join(", ")}
      FROM sessions
      WHERE started_at >= ? AND started_at < ?
      ORDER BY started_at ASC
      `,
    )
    .all(periodStart, periodEnd) as SessionRow[];

  const liveColumns = ["app_name", "category", "started_at"].concat(
    ["source_host", "window_title"].filter((column) => sessionColumns.has(column)),
  );
  const live = db
    .prepare(
      `
      SELECT ${liveColumns.join(", ")}
      FROM sessions
      WHERE ended_at IS NULL
      ORDER BY started_at DESC LIMIT 1
      `,
    )
    .get() as
    | {
        app_name: string;
        category: string;
        started_at: number;
        source_host?: string | null;
        window_title?: string | null;
      }
    | undefined;

  const hasCaptures = tableExists(db, "captures");
  const captures = hasCaptures
    ? (db
        .prepare(
          `
          SELECT captured_at, app_name, window_title, source_host, input_tokens, text_excerpt, status
          FROM captures
          WHERE captured_at >= ? AND captured_at < ? AND input_tokens > 0
          ORDER BY captured_at DESC
          LIMIT 12
          `,
        )
        .all(periodStart, periodEnd) as CaptureStat[])
    : [];

  const statusRows = hasCaptures
    ? (db
        .prepare(
          `
          SELECT status, COUNT(*) AS count
          FROM captures
          WHERE captured_at >= ? AND captured_at < ?
          GROUP BY status
          `,
        )
        .all(periodStart, periodEnd) as Array<{ status: string | null; count: number }>)
    : [];

  db.close();

  let outputTokens = 0;
  let inputTokens = 0;
  let textInputTokens = 0;
  let rateInputTokens = 0;
  let totalSeconds = 0;
  let productiveSeconds = 0;
  let consumingSeconds = 0;
  let keystrokesTotal = 0;
  let captureCount = 0;
  let lastCaptureStatus = "";

  const appMap = new Map<string, AppStat>();
  const categoryMap = new Map<string, CategoryStat>();
  const hourMap = new Map<number, HourlyStat>();

  for (const session of sessions) {
    const duration = numberValue(session.duration_seconds);
    const keystrokes = numberValue(session.keystrokes);
    const tokens = computeSessionTokens(session);
    const host = session.source_host ?? "";
    const windowTitle = cleanTitle(session.window_title);
    const appKey = `${session.app_name}|${host}|${windowTitle}|${session.category}`;
    const displayName = windowTitle || host || session.app_name;

    outputTokens += tokens.outputTokens;
    inputTokens += tokens.inputTokens;
    textInputTokens += tokens.textInputTokens;
    rateInputTokens += tokens.rateInputTokens;
    totalSeconds += duration;
    keystrokesTotal += keystrokes;
    captureCount += numberValue(session.capture_count);

    if (session.last_capture_status) lastCaptureStatus = session.last_capture_status;
    if (session.category === "creating") productiveSeconds += duration;
    if (CONSUMING_CATEGORIES.has(session.category)) {
      consumingSeconds += duration;
    }

    const existingCategory = categoryMap.get(session.category);
    if (existingCategory) {
      existingCategory.totalSeconds += duration;
      existingCategory.inputTokens += tokens.inputTokens;
      existingCategory.outputTokens += tokens.outputTokens;
    } else {
      categoryMap.set(session.category, {
        category: session.category,
        totalSeconds: duration,
        inputTokens: tokens.inputTokens,
        outputTokens: tokens.outputTokens,
      });
    }

    const existing = appMap.get(appKey);
    if (existing) {
      existing.totalSeconds += duration;
      existing.keystrokes += keystrokes;
      existing.outputTokens += tokens.outputTokens;
      existing.inputTokens += tokens.inputTokens;
      existing.textInputTokens += tokens.textInputTokens;
      existing.rateInputTokens += tokens.rateInputTokens;
    } else {
      appMap.set(appKey, {
        key: appKey,
        app_name: session.app_name,
        display_name: displayName,
        window_title: windowTitle,
        source_host: host,
        category: session.category,
        totalSeconds: duration,
        keystrokes,
        outputTokens: tokens.outputTokens,
        inputTokens: tokens.inputTokens,
        textInputTokens: tokens.textInputTokens,
        rateInputTokens: tokens.rateInputTokens,
      });
    }

    const hour = new Date(session.started_at * 1000).getHours();
    const hExisting = hourMap.get(hour);
    if (hExisting) {
      hExisting.outputTokens += tokens.outputTokens;
      hExisting.inputTokens += tokens.inputTokens;
    } else {
      hourMap.set(hour, {
        hour,
        outputTokens: tokens.outputTokens,
        inputTokens: tokens.inputTokens,
      });
    }
  }

  const statusCounts = Object.fromEntries(
    statusRows.map((row) => [row.status || "unknown", Number(row.count || 0)]),
  );

  const apps = Array.from(appMap.values())
    .sort((a, b) => b.totalSeconds - a.totalSeconds)
    .slice(0, 15);

  const categories = Array.from(categoryMap.values()).sort(
    (a, b) => b.totalSeconds - a.totalSeconds,
  );

  const hourly = Array.from(hourMap.values()).sort((a, b) => a.hour - b.hour);

  const liveHost = live?.source_host ?? "";
  const liveTitle = cleanTitle(live?.window_title);
  const currentApp = live
    ? {
        app_name: live.app_name,
        display_name: liveTitle || liveHost || live.app_name,
        window_title: liveTitle,
        category: live.category,
        source_host: liveHost,
        seconds: Math.floor(Date.now() / 1000) - live.started_at,
      }
    : null;

  return {
    outputTokens,
    inputTokens,
    textInputTokens,
    rateInputTokens,
    totalSeconds,
    productiveSeconds,
    consumingSeconds,
    keystrokesTotal,
    apps,
    categories,
    hourly,
    captures,
    captureHealth: {
      captureCount,
      capturedTextTokens: textInputTokens,
      rateInputTokens,
      lastCaptureStatus,
      statusCounts,
    },
    currentApp,
    dbExists: true,
  };
}
