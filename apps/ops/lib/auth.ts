// Password-gated session for the ops dashboard.
//
// The session is a signed cookie: `<exp>.<hmac(exp)>`. Verification only needs
// the shared secret, so it works in the Edge middleware and in Node route
// handlers alike. No token is ever sent to the browser.

export const SESSION_COOKIE = "ops_session";
const SESSION_TTL_MS = 1000 * 60 * 60 * 12; // 12h

function secret(): string {
  const s = process.env.OPS_SESSION_SECRET || process.env.OPS_PASSWORD || "";
  if (!s) throw new Error("OPS_SESSION_SECRET (or OPS_PASSWORD) is not set");
  return s;
}

function b64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmac(payload: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return b64url(sig);
}

/** Constant-time-ish string compare. */
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Verifies the configured password against the submitted one. */
export function checkPassword(submitted: string): boolean {
  const expected = process.env.OPS_PASSWORD || "";
  if (!expected) return false;
  return safeEqual(submitted, expected);
}

/** Builds a fresh signed session cookie value. */
export async function createSession(): Promise<string> {
  const exp = String(Date.now() + SESSION_TTL_MS);
  return `${exp}.${await hmac(exp)}`;
}

/** True when the cookie is well-formed, unexpired, and correctly signed. */
export async function verifySession(value: string | undefined | null): Promise<boolean> {
  if (!value) return false;
  const dot = value.indexOf(".");
  if (dot <= 0) return false;
  const exp = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  const expNum = Number(exp);
  if (!Number.isFinite(expNum) || expNum < Date.now()) return false;
  try {
    return safeEqual(sig, await hmac(exp));
  } catch {
    return false;
  }
}

export const sessionCookieOptions = {
  httpOnly: true,
  sameSite: "lax" as const,
  secure: process.env.NODE_ENV === "production",
  path: "/",
  maxAge: SESSION_TTL_MS / 1000,
};
