// Server-side client for the platform-admin API. The bearer token stays
// server-side (never shipped to the browser).
const BASE = process.env.ADMIN_URL || "http://127.0.0.1:8002";
const TOKEN = process.env.ADMIN_TOKEN || "";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`admin ${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export type Guild = { id: number; name: string | null };
export type Route = {
  id: number;
  module: string;
  event_type: string;
  rule: unknown;
  priority: number;
  channel_id: number | null;
  thread_id: number | null;
  enabled: boolean;
};
export type Source = {
  id: string;
  module: string;
  category: string;
  enabled: boolean;
  interval_minutes: number;
  last_success_at: string | null;
  error_streak: number;
};

export const getGuilds = () => api<Guild[]>("/api/platform/guilds");
export const getRoutes = () => api<Route[]>("/api/platform/routes");
export const getSources = () => api<Source[]>("/api/platform/sources");
export const getOutboxStats = () => api<Record<string, number>>("/api/platform/outbox/stats");

export type ManhwaConfig = {
  guild_id: string;
  channel_id: string | null;
  raw_channel_id: string | null;
  whitelist_channel_id: string | null;
  origin_filter: string | null;
  label: string | null;
  type_filter: string | null;
  updated_at: string | null;
};
export type ManhwaHealth = {
  source: string;
  status: string;
  consecutive_failures: number;
  last_error: string | null;
  last_success_at: string | null;
  response_time_ms: number | null;
};
export const getManhwaConfig = () => api<ManhwaConfig[]>("/api/platform/manhwa/config");
export const getManhwaHealth = () => api<ManhwaHealth[]>("/api/platform/manhwa/health");

export type ManhwaFeeds = {
  guild_id: string;
  raw_channel: string;
  legacy_channel: string;
  shared_whitelist: string;
  shinigami_whitelist: string;
  ikiru_whitelist: string;
};
export const getManhwaFeeds = () => api<ManhwaFeeds>("/api/platform/manhwa/feeds");

export type MediaConfig = {
  pahe: {
    thread_2026: string;
    thread_2025: string;
    thread_horror: string;
    fallback_channel: string;
  };
  ovagames: { thread: string };
  skidrow: { thread: string };
  game3rb: { thread: string };
  drakorasia: { korea: string; china: string };
};
export const getMediaConfig = () => api<MediaConfig>("/api/platform/media/config");
