import LogoutButton from "./LogoutButton";
import ManhwaConfigPanel from "./ManhwaConfigPanel";
import SourcesPanel from "./SourcesPanel";
import { getGuilds, getManhwaConfig, getManhwaHealth, getSources } from "@/lib/api";

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
  const [guildsR, sourcesR, mhCfgR, mhHealthR] = await Promise.all([
    safe(getGuilds),
    safe(getSources),
    safe(getManhwaConfig),
    safe(getManhwaHealth),
  ]);

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="flex items-center justify-between">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">media-platform · bot config</h1>
          <span className="text-xs text-neutral-500">unified Discord bot dashboard</span>
        </div>
        <LogoutButton />
      </header>

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

      <Card title={`Guilds · ${guildsR.data?.length ?? 0} connected`}>
        {guildsR.error ? (
          <Err error={guildsR.error} />
        ) : !guildsR.data || guildsR.data.length === 0 ? (
          <div className="space-y-1">
            <span className="text-neutral-500">No guilds yet.</span>
            <p className="text-xs text-neutral-600">Guilds appear once the bot joins a server.</p>
          </div>
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
    </main>
  );
}
