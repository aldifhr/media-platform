import { NextRequest, NextResponse } from "next/server";

// Proxies PATCH to the admin API with the server-side bearer token.
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.text();
  const res = await fetch(`${process.env.ADMIN_URL}/api/platform/sources/${id}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${process.env.ADMIN_TOKEN}`,
      "Content-Type": "application/json",
    },
    body,
  });
  return NextResponse.json(await res.json(), { status: res.status });
}
