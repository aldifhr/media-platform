import { NextResponse } from "next/server";

// Proxies GET to the admin API with the server-side bearer token.
export async function GET() {
  const res = await fetch(`${process.env.ADMIN_URL}/api/platform/manhwa/config`, {
    headers: { Authorization: `Bearer ${process.env.ADMIN_TOKEN}` },
    cache: "no-store",
  });
  return NextResponse.json(await res.json(), { status: res.status });
}
