import { NextRequest, NextResponse } from "next/server";
import { getStats } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const period = searchParams.get("period") ?? "today";

  const now = Math.floor(Date.now() / 1000);
  let start: number;
  let end: number = now + 86400;

  if (period === "today") {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    start = Math.floor(d.getTime() / 1000);
    end = start + 86400;
  } else if (period === "week") {
    start = now - 7 * 86400;
  } else {
    start = now - 86400; // last 24h
  }

  const stats = getStats(start, end);
  return NextResponse.json(stats);
}
