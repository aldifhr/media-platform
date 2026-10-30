import { Activity, Rss, Server, ServerCog, Users } from "lucide-react";
import LogoutButton from "./LogoutButton";
import ManhwaConfigPanel from "./ManhwaConfigPanel";
import MediaConfigPanel from "./MediaConfigPanel";
import SourcesPanel, { SourcesError } from "./SourcesPanel";
import { Reveal, StaggerGroup, StaggerItem } from "./components/motion";
import { Stat } from "./components/stats";
import { Card, CardHeader, ErrorState, StatusPill } from "./components/ui";
import {
  getGuilds,
  getManhwaConfig,
  getManhwaFeeds,
  getManhwaHealth,
  getMediaConfig,
  getSources,
} from "@/lib/api";

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
  const [guildsR, sourcesR, mhCfgR, mhHealthR, mhFeedsR, mediaCfgR] = await Promise.all([
    safe(getGuilds),
    safe(getSources),
    safe(getManhwaConfig),
    safe(getManhwaHealth),
    safe(getManhwaFeeds),
    safe(getMediaConfig),
  ]);

  const sources = sourcesR.data || [];
  const health = mhHealthR.data || [];
  const sourcesUp = sources.filter((s) => s.enabled && s.error_streak === 0).length;
  const sourcesDown = sources.filter((s) => s.error_streak > 0).length;
  const healthBad = health.filter((h) => h.status.toUpperCase() !== "HEALTHY").length;
  const allHealthy =
    sources.length > 0 && sourcesDown === 0 && healthBad === 0 && !sourcesR.error;

  return (
    <div className="min-h-screen">
      <TopBar healthy={allHealthy} />

      <main className="mx-auto max-w-6xl px-5 pb-20 pt-10 sm:px-8">
        <Reveal>
          <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-faint">
                <span className="h-px w-5 bg-border-strong" />
                Overview
              </div>
              <h1 className="mt-3 text-[28px] font-semibold leading-none tracking-tight text-fg">
                Bot control center
              </h1>
              <p className="mt-2.5 max-w-lg text-sm leading-relaxed text-muted">
                Live status for content sources and Discord bot configuration across the platform.
              </p>
            </div>
          </div>
        </Reveal>

        <StaggerGroup className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StaggerItem>
            <Stat
              label="Sources up"
              value={`${sourcesUp}/${sources.length}`}
              tone={sourcesDown > 0 ? "amber" : "green"}
              hint={sourcesDown > 0 ? `${sourcesDown} reporting errors` : "all healthy"}
              icon={<Rss className="h-3.5 w-3.5" />}
            />
          </StaggerItem>
          <StaggerItem>
            <Stat
              label="Source health"
              value={health.length === 0 ? "—" : `${health.length - healthBad}/${health.length}`}
              tone={healthBad > 0 ? "amber" : health.length ? "green" : "neutral"}
              hint={health.length ? `${healthBad} degraded` : "no data"}
              icon={<Activity className="h-3.5 w-3.5" />}
            />
          </StaggerItem>
          <StaggerItem>
            <Stat
              label="Guilds"
              value={guildsR.data?.length ?? "—"}
              hint="connected"
              icon={<Users className="h-3.5 w-3.5" />}
            />
          </StaggerItem>
        </StaggerGroup>

        <div className="mt-10 space-y-8">
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
                <CardHeader
                  icon={<ServerCog className="h-4 w-4" />}
                  title="Manhwa config"
                  subtitle="Channel routing per guild"
                />
                <ErrorState error={mhCfgR.error || mhHealthR.error || ""} />
              </Card>
            ) : (
              <ManhwaConfigPanel
                config={mhCfgR.data || []}
                health={health}
                feeds={mhFeedsR.data}
              />
            )}
          </Reveal>

          <Reveal delay={0.15}>
            {mediaCfgR.error ? (
              <Card>
                <CardHeader
                  icon={<Server className="h-4 w-4" />}
                  title="Media delivery"
                  subtitle="Discord threads per media source"
                />
                <ErrorState error={mediaCfgR.error} />
              </Card>
            ) : (
              <MediaConfigPanel config={mediaCfgR.data} />
            )}
          </Reveal>
        </div>

        <footer className="mt-16 flex items-center justify-between border-t border-border pt-5 text-[11px] text-faint">
          <span className="font-mono">
            {sources.length} sources · {guildsR.data?.length ?? 0} guilds
          </span>
        </footer>
      </main>
    </div>
  );
}

function TopBar({ healthy }: { healthy: boolean }) {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-bg/70 backdrop-blur-xl">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3.5 sm:px-8">
        <div className="flex items-baseline gap-2.5">
          <span className="bg-gradient-to-br from-emerald-300 to-sky-400 bg-clip-text text-[15px] font-semibold tracking-tight text-transparent">
            MediaPlatform
          </span>
        
        </div>
        <div className="flex items-center gap-3">
          <StatusPill healthy={healthy} />
          <LogoutButton />
        </div>
      </div>
    </header>
  );
}
