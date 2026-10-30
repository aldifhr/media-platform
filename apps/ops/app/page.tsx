import ManhwaConfigPanel from "./ManhwaConfigPanel";
import SourcesPanel from "./SourcesPanel";
import {
  getGuilds,
  getManhwaConfig,
  getManhwaHealth,
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

type Res<T> = { data: T | null; error: string | null };

// Fetch independently so one failure never masks the others, and so a real
// error (401/network) is shown as an error — never as a fake "empty" state.
async function safe<T>(fn: () => Promise<T>): Promise<Res<T>> {
  try {
    return { data: await fn(), error: null };
  } catch (e) {
    return { data: null, error: e instanceof Error ? e.message : String(e) };
  }
}

function Err({ error }: { error: string }) {
  return <span className="font-mono text-xs text-red-400">fetch error: {error}</span>;
}

export default async function Dashboard() {
  const [guildsR, subsR, routesR, sourcesR, statsR, mhCfgR, mhHealthR] = await Promise.all([
    safe(getGuilds),
    safe(getSubscriptions),
    safe(getRoutes),
    safe(getSources),
    safe(getOutboxStats),
    safe(getManhwaConfig),
    safe(getManhwaHealth),
  ]);
  const stats = statsR.data || {};

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">media-platform · bot config</h1>
        <span className="text-xs text-neutral-500">unified Discord bot dashboard</span>
      </header>

      <Card title="Outbox">
        {statsR.error ? (
          <Err error={statsR.error} />
        ) : Object.keys(stats).length === 0 ? (
          <span className="text-neutral-500">empty</span>
        ) : (
          <div className="flex gap-6 font-mono text-xs">
            {Object.entries(stats).map(([k, v]) => (
              <span key={k}>
                <span className="text-neutral-400">{k}</span>: <span className="text-neutral-100">{v}</span>
              </span>
            ))}
          </div>
        )}
      </Card>

      {sourcesR.error ? (
        <Card title="Sources">
          <Err error={sourcesR.error} />
        </Card>
      ) : (
        <SourcesPanel sources={sourcesR.data || []} />
      )}

      {mhCfgR.error || mhHealthR.error ? (
        <Card title="Manhwa bot config">
          <Err error={mhCfgR.error || mhHealthR.error || ""} />
        </Card>
      ) : (
        <ManhwaConfigPanel config={mhCfgR.data || []} health={mhHealthR.data || []} />
      )}

      <Card title="Guilds">
        {guildsR.error ? (
          <Err error={guildsR.error} />
        ) : !guildsR.data || guildsR.data.length === 0 ? (
          <span className="text-neutral-500">No guilds yet.</span>
        ) : (
          <ul className="space-y-1 font-mono text-xs">
            {guildsR.data.map((g) => (
              <li key={g.id}>
                {g.id} — {g.name || "(unnamed)"}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Subscriptions">
        {subsR.error ? (
          <Err error={subsR.error} />
        ) : !subsR.data || subsR.data.length === 0 ? (
          <span className="text-neutral-500">No subscriptions configured.</span>
        ) : (
          <ul className="space-y-1 font-mono text-xs">
            {subsR.data.map((s) => (
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
        {routesR.error ? (
          <Err error={routesR.error} />
        ) : !routesR.data || routesR.data.length === 0 ? (
          <span className="text-neutral-500">No routes configured.</span>
        ) : (
          <ul className="space-y-1 font-mono text-xs">
            {routesR.data.map((r) => (
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
