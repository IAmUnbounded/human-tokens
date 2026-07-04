import { execFile } from "child_process";
import { existsSync } from "fs";
import path from "path";
import { promisify } from "util";
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);
const ROOT = path.join(process.cwd(), "..");
const LABEL = "com.local.human-token-tracker";
const UID = typeof process.getuid === "function" ? process.getuid() : 0;
const SERVICE = `gui/${UID}/${LABEL}`;
const PLIST = path.join(process.env.HOME ?? "", "Library", "LaunchAgents", `${LABEL}.plist`);
const PID_FILE = path.join(ROOT, ".tracker.pid");

type TrackerStatus = {
  running: boolean;
  mode: "launchagent" | "pidfile" | "stopped";
  pid: number | null;
  detail: string;
};

function parseLaunchctl(output: string): TrackerStatus {
  const state = output.match(/\bstate = ([^\n]+)/)?.[1]?.trim() ?? "";
  const pidText = output.match(/\bpid = (\d+)/)?.[1];
  const pid = pidText ? Number(pidText) : null;
  const running = state === "running" && !!pid;

  return {
    running,
    mode: "launchagent",
    pid,
    detail: running ? "LaunchAgent running" : `LaunchAgent ${state || "loaded"}`,
  };
}

async function commandOk(cmd: string, args: string[], cwd = ROOT) {
  try {
    const result = await execFileAsync(cmd, args, { cwd, timeout: 8_000 });
    return { ok: true, output: `${result.stdout}${result.stderr}`.trim() };
  } catch (error) {
    const err = error as { stdout?: string; stderr?: string; message?: string };
    return {
      ok: false,
      output: `${err.stdout ?? ""}${err.stderr ?? ""}${err.message ?? ""}`.trim(),
    };
  }
}

async function pidAlive(pid: number) {
  const result = await commandOk("kill", ["-0", String(pid)]);
  return result.ok;
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function getPidFileStatus(): Promise<TrackerStatus> {
  if (!existsSync(PID_FILE)) {
    return { running: false, mode: "stopped", pid: null, detail: "Tracker stopped" };
  }

  const result = await commandOk("cat", [PID_FILE]);
  const pid = Number(result.output.trim());
  if (Number.isFinite(pid) && (await pidAlive(pid))) {
    return { running: true, mode: "pidfile", pid, detail: "Background process running" };
  }

  return { running: false, mode: "stopped", pid: null, detail: "Stale PID file" };
}

async function getStatus(): Promise<TrackerStatus> {
  if (existsSync(PLIST)) {
    const result = await commandOk("launchctl", ["print", SERVICE]);
    if (result.ok) return parseLaunchctl(result.output);

    const pidStatus = await getPidFileStatus();
    if (pidStatus.running) return pidStatus;
    return { running: false, mode: "launchagent", pid: null, detail: "LaunchAgent stopped" };
  }

  return getPidFileStatus();
}

async function waitForState(running: boolean): Promise<TrackerStatus> {
  let status = await getStatus();

  for (let attempt = 0; attempt < 10 && status.running !== running; attempt += 1) {
    await sleep(750);
    status = await getStatus();
  }

  return status;
}

async function startTracker() {
  if (existsSync(PLIST)) {
    await commandOk("launchctl", ["bootstrap", `gui/${UID}`, PLIST]);
    await commandOk("launchctl", ["enable", SERVICE]);
    await commandOk("launchctl", ["kickstart", "-k", SERVICE]);
    return;
  }

  await commandOk(path.join(ROOT, "scripts", "start_background.sh"), []);
}

async function stopTracker() {
  if (existsSync(PLIST)) {
    await commandOk("launchctl", ["bootout", `gui/${UID}`, PLIST]);
    return;
  }

  await commandOk(path.join(ROOT, "scripts", "stop_background.sh"), []);
}

export async function GET() {
  return NextResponse.json(await getStatus());
}

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => ({}))) as { action?: string };

  if (body.action === "start") {
    await startTracker();
    return NextResponse.json(await waitForState(true));
  } else if (body.action === "stop") {
    await stopTracker();
    return NextResponse.json(await waitForState(false));
  } else {
    return NextResponse.json({ error: "Unsupported tracker action" }, { status: 400 });
  }
}
