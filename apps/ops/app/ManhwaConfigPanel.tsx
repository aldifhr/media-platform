"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Activity, Loader2, Save, ServerCog } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { ManhwaConfig, ManhwaFeeds, ManhwaHealth } from "@/lib/api";
import { useToast } from "./components/Toast";
import { Badge, Card, CardHeader, Dot, EmptyState } from "./components/ui";
import { cn } from "@/lib/utils";

const INPUT =
  "w-full rounded-lg border border-border-strong bg-bg/60 px-2.5 py-1.5 font-mono text-xs text-fg outline-none transition-colors placeholder:text-faint focus:border-faint focus:ring-2 focus:ring-border-strong/60";

function healthTone(status: string): "green" | "amber" | "red" {
  const s = status.toUpperCase();
  if (s === "HEALTHY") return "green";
  if (s === "DEGRADED") return "amber";
  return "red";
}

function ReadOnlyField({ label, value }: { label: string; value?: string }) {
  return (
    <div className="block">
      <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.1em] text-faint">
        {label}
      </span>
      <code className="block rounded-lg border border-border bg-bg/40 px-2.5 py-1.5 font-mono text-xs text-fg">
        {value || "—"}
      </code>
    </div>
  );
}

function GuildForm({
  g,
  feeds,
  busy,
  onSave,
}: {
  g: ManhwaConfig;
  feeds: ManhwaFeeds | null;
  busy: boolean;
  onSave: (g: ManhwaConfig, raw: string, legacy: string) => void;
}) {
  const [raw, setRaw] = useState(g.raw_channel_id || "");
  const [legacy, setLegacy] = useState(g.channel_id || "");

  return (
    <div className="grid gap-4 px-5 py-4 sm:grid-cols-[1fr_auto] sm:items-end">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.1em] text-faint">
            Raw channel
          </span>
          <input
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            className={INPUT}
            placeholder="—"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.1em] text-faint">
            Legacy channel
          </span>
          <input
            value={legacy}
            onChange={(e) => setLegacy(e.target.value)}
            className={INPUT}
            placeholder="—"
          />
        </label>
        <ReadOnlyField label="Shinigami whitelist" value={feeds?.shinigami_whitelist} />
        <ReadOnlyField label="Ikiru whitelist" value={feeds?.ikiru_whitelist} />
      </div>
      <motion.button
        whileTap={{ scale: 0.97 }}
        onClick={() => onSave(g, raw, legacy)}
        disabled={busy}
        className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-fg px-4 text-xs font-semibold text-bg transition-colors hover:bg-white disabled:opacity-50"
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
        {busy ? "Saving" : "Save"}
      </motion.button>
    </div>
  );
}

export default function ManhwaConfigPanel({
  config,
  health,
  feeds,
}: {
  config: ManhwaConfig[];
  health: ManhwaHealth[];
  feeds: ManhwaFeeds | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);

  async function save(g: ManhwaConfig, raw: string, legacy: string) {
    setBusy(g.guild_id);
    try {
      const res = await fetch(`/api/manhwa/config/${g.guild_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_channel_id: raw, channel_id: legacy }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.push("ok", `Saved channels for guild ${g.guild_id}`);
      router.refresh();
    } catch (e) {
      toast.push("err", `Save failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader
        icon={<ServerCog className="h-4 w-4" />}
        title="Manhwa config"
        subtitle={`${config.length} guild${config.length === 1 ? "" : "s"} configured`}
      />

      {config.length === 0 ? (
        <EmptyState
          icon={<ServerCog className="h-5 w-5" />}
          title="No manhwa guild configured"
          hint="A guild appears here once the manhwa bot is invited and configured."
        />
      ) : (
        <div className="divide-y divide-border/60">
          <AnimatePresence initial={false}>
            {config.map((g) => (
              <motion.div
                key={g.guild_id}
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              >
                <div className="flex items-center gap-2 px-5 pt-4">
                  <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-faint">
                    Guild
                  </span>
                  <code className="rounded bg-bg/60 px-1.5 py-0.5 font-mono text-[11px] text-fg">
                    {g.guild_id}
                  </code>
                  {g.label && <Badge tone="blue">{g.label}</Badge>}
                </div>
                <GuildForm g={g} feeds={feeds} busy={busy === g.guild_id} onSave={save} />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}

      {health.length > 0 && (
        <div className="border-t border-border px-5 py-4">
          <div className="mb-2.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.1em] text-faint">
            <Activity className="h-3.5 w-3.5" />
            Source health
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {health.map((h) => {
              const tone = healthTone(h.status);
              return (
                <motion.div
                  key={h.source}
                  whileHover={{ x: 2 }}
                  className={cn(
                    "flex items-center justify-between rounded-lg border bg-bg/40 px-3 py-2",
                    tone === "green"
                      ? "border-border"
                      : tone === "amber"
                        ? "border-amber-500/25"
                        : "border-red-500/25",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <Dot tone={tone} pulse={tone !== "green"} />
                    <span className="font-mono text-xs text-fg">{h.source}</span>
                  </div>
                  <div className="flex items-center gap-2 text-[11px] text-faint">
                    <span className="tabular-nums">{h.response_time_ms ?? "—"}ms</span>
                    {h.consecutive_failures > 0 && (
                      <Badge tone="red">{h.consecutive_failures} fails</Badge>
                    )}
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      )}
    </Card>
  );
}
