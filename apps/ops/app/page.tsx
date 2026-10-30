import {
  Activity,
  LayoutDashboard,
  Rss,
  Server,
  Users,
} from "lucide-react";
import LogoutButton from "./LogoutButton";
import ManhwaConfigPanel from "./ManhwaConfigPanel";
import SourcesPanel, { SourcesError } from "./SourcesPanel";
import { Reveal, StaggerGroup, StaggerItem } from "./components/motion";
import { Stat } from "./components/stats";
import { Card, CardHeader, ErrorState } from "./components/ui";
import { getGuilds, getManhwaConfig, getManhwaHealth, getSources } from "@/lib/api";

export const dynamic = "force-dynamic";

type Res<T> = { data: T | null; error: string | null };

async function safe<T>(fn: () => Promise<T>): Promise<Res<T>> {
  try {
    return { data: await fn(), error: null };
  } catch (e) {
    return { data: null, error: e instanceof Error ? e.message : String(e) };
  }
}

export default async function Dashboard() {
  const [guildsR, sourcesR, mhCfgR, mhHealthR] = await Promise.all([
    safe(getGuilds),
    safe(getSources),
    safe(getManhwaConfig),
    safe(getManhwaHealth),
  ]);

  const sources = sourcesR.data || [];
  const health = mhHealthR.data || [];
  const sourcesUp = sources.filter((s) => s.enabled && s.error_streak === 0).length;
  const sourcesDown = sources.filter((s) => s.error_streak > 0).length;
  const healthBad = health.filter((h) => h.status.toUpperCase() !== "HEALTHY").length;
  const allHealthy = sources.length > 0 && sourcesDown === 0 && healthBad === 0;

  return (
    <div className="min-h-screen">
      <TopBar healthy={allHealthy} />
      <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8">
        <Reveal className="mb-7">
          <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-[#5c5c64]">
            <LayoutDashboard className="h-3.5 w-3.5" />
            Overview
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-[#ededf0]">
            Bot control center
          </h1>
          <p className="mt-1 max-w-xl text-sm text-[#8b8b93]">
            Live status for sources and Discord bot configuration across the platform.
          </p>
        </Reveal>

        <StaggerGroup className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StaggerItem>
            <Stat
              label="Sources up"
              value={`${sourcesUp}/${sources.length}`}
              tone={sourcesDown > 0 ? "amber" : "green"}
              hint={sourcesDown > 0 ? `${sourcesDown} with errors` : "all healthy"}
              icon={<Rss className="h-3 w-3" />}
            />
          </StaggerItem>
          <StaggerItem>
            <Stat
              label="Source health"
              value={health.length === 0 ? "—" : `${health.length - healthBad}/${health.length}`}
              tone={healthBad > 0 ? "amber" : health.length ? "green" : "neutral"}
              hint={health.length ? `${healthBad} degraded` : "no data"}
              icon={<Activity className="h-3 w-3" />}
            />
          </StaggerItem>
          <StaggerItem>
            <Stat
              label="Guilds"
              value={guildsR.data?.length ?? "—"}
              hint="connected"
              icon={<Users className="h-3 w-3" />}
            />
          </StaggerItem>
        </StaggerGroup>

        <div className="mt-6 space-y-6">
          <Reveal delay={0.05}>
            {sourcesR.error ? (
              <SourcesError error={sourcesR.error} />
            ) : (
              <SourcesPanel sources={sources} />
            )}
          </Reveal>

          <Reveal delay={0.1}>
            {mhCfgR.error || mhHealthR.error ? (
              <Card>
                <CardHeader icon={<Server className="h-4 w-4" />} title="Manhwa config" />
                <ErrorState error={mhCfgR.error || mhHealthR.error || ""} />
              </Card>
            ) : (
              <ManhwaConfigPanel config={mhCfgR.data || []} health={health} />
            )}
          </Reveal>

          <Reveal delay={0.15}>
            <Card>
              <CardHeader
                icon={<Users className="h-4 w-4" />}
                title="Guilds"
                subtitle={`${guildsR.data?.length ?? 0} connected`}
              />
              {guildsR.error ? (
                <ErrorState error={guildsR.error} />
              ) : !guildsR.data || guildsR.data.length === 0 ? (
                <div className="space-y-1 px-5 py-4">
                  <span className="text-sm text-[#8b8b93]">No guilds yet.</span>
                  <p className="text-xs text-[#5c5c64]">
                    Guilds appear once the bot joins a server.
                  </p>
                </div>
              ) : (
                <StaggerGroup className="divide-y divide-[#26262b]/60">
                  {guildsR.data.map((g) => (
                    <StaggerItem key={g.id}>
                      <div className="flex items-center justify-between gap-3 px-5 py-3">
                        <span className="truncate text-sm text-[#ededf0]">
                          {g.name || <span className="text-[#5c5c64]">(unnamed)</span>}
                        </span>
                        <code className="shrink-0 font-mono text-[11px] text-[#5c5c64]">
                          {g.id}
                        </code>
                      </div>
                    </StaggerItem>
                  ))}
                </StaggerGroup>
              )}
            </Card>
          </Reveal>
        </div>
      </main>
    </div>
  );
}

function TopBar({ healthy }: { healthy: boolean }) {
  return (
    <header className="sticky top-0 z-30 border-b border-[#26262b] bg-[#0a0a0b]/70 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3 sm:px-8">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-400/90 to-sky-500/90 text-[13px] font-bold text-[#0a0a0b] shadow-lg shadow-emerald-500/10">
            m
          </span>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-[#ededf0]">media-platform</div>
            <div className="text-[11px] text-[#5c5c64]">bot config</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden items-center gap-1.5 rounded-full border border-[#26262b] bg-[#111113]/60 px-2.5 py-1 text-[11px] text-[#8b8b93] sm:inline-flex">
            <span
              className={
                "h-1.5 w-1.5 rounded-full " + (healthy ? "bg-emerald-400" : "bg-amber-400")
              }
            />
            {healthy ? "all systems healthy" : "attention needed"}
          </span>
          <LogoutButton />
        </div>
      </div>
    </header>
  );
}
