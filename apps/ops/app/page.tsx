import SourcesPanel from "./SourcesPanel";
import {
  getGuilds,
  getOutboxStats,
  getRoutes,
  getSources,
  getSubscriptions,
} from "@/lib/api";

export const dynamic = "force-dynamic";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-neutral-800 bg-neutral-900/50">
      <h2 className="border-b border-neutral-800 px-4 py-3 text-sm font-medium text-neutral-300">
        {title}
      </h2>
      <div className="px-4 py-3 text-sm text-neutral-200">{children}</div>
    </section>
  );
}

export default async function Dashboard() {
  let guilds: Awaited<ReturnType<typeof getGuilds>> = [];
  let subs: Awaited<ReturnType<typeof getSubscriptions>> = [];
  let routes: Awaited<ReturnType<typeof getRoutes>> = [];
  let sources: Awaited<ReturnType<typeof getSources>> = [];
  let stats: Record<string, number> = {};
  let error: string | null = null;
  try {
    [guilds, subs, routes, sources, stats] = await Promise.all([
      getGuilds(),
      getSubscriptions(),
      getRoutes(),
      getSources(),
      getOutboxStats(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "admin API unreachable";
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">media-platform · bot config</h1>
        <span className="text-xs text-neutral-500">unified Discord bot dashboard</span>
      </header>

      {error && (
        <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <Card title="Outbox">
        <div className="flex gap-6 font-mono text-xs">
          {Object.keys(stats).length === 0 && <span className="text-neutral-500">empty</span>}
          {Object.entries(stats).map(([k, v]) => (
            <span key={k}>
              <span className="text-neutral-400">{k}</span>: <span className="text-neutral-100">{v}</span>
            </span>
          ))}
        </div>
      </Card>

      <SourcesPanel sources={sources} />

      <Card title="Guilds">
        {guilds.length === 0 ? (
          <span className="text-neutral-500">No guilds yet.</span>
        ) : (
          <ul className="space-y-1 font-mono text-xs">
            {guilds.map((g) => (
              <li key={g.id}>
                {g.id} — {g.name || "(unnamed)"}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Subscriptions">
        {subs.length === 0 ? (
          <span className="text-neutral-500">No subscriptions configured.</span>
        ) : (
          <ul className="space-y-1 font-mono text-xs">
            {subs.map((s) => (
              <li key={s.id}>
                guild {s.guild_id} · {s.module}
                {s.category ? `/${s.category}` : ""} → channel {s.channel_id}{" "}
                <span className={s.enabled ? "text-emerald-400" : "text-neutral-500"}>
                  {s.enabled ? "on" : "off"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Notification routes">
        {routes.length === 0 ? (
          <span className="text-neutral-500">No routes configured.</span>
        ) : (
          <ul className="space-y-1 font-mono text-xs">
            {routes.map((r) => (
              <li key={r.id}>
                p{r.priority} · {r.module} · {r.event_type} →{" "}
                {r.thread_id ? `thread ${r.thread_id}` : `channel ${r.channel_id}`}{" "}
                <span className={r.enabled ? "text-emerald-400" : "text-neutral-500"}>
                  {r.enabled ? "on" : "off"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </main>
  );
}
