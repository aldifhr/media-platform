import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { SESSION_COOKIE, checkPassword, createSession, sessionCookieOptions } from "@/lib/auth";

export async function POST(req: NextRequest) {
  const form = await req.formData();
  const password = String(form.get("password") || "");

  if (!checkPassword(password)) {
    return NextResponse.redirect(new URL("/login?error=1", req.url), { status: 303 });
  }

  const value = await createSession();
  const jar = await cookies();
  jar.set(SESSION_COOKIE, value, sessionCookieOptions);
  return NextResponse.redirect(new URL("/", req.url), { status: 303 });
}
