"use client";

import { motion } from "framer-motion";
import { Rss, Radio } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Source } from "@/lib/api";
import { useToast } from "./components/Toast";
import { Toggle } from "./components/controls";
import { Badge, Card, CardHeader, EmptyState, ErrorState } from "./components/ui";

const MODULE_TONE: Record<string, "blue" | "violet" | "green" | "amber"> = {
  media: "blue",
  manhwa: "violet",
};

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "never";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export default function SourcesPanel({ sources }: { sources: Source[] }) {
  const router = useRouter();
  const toast = useToast();
  const [pending, setPending] = useState<string | null>(null);

  async function toggle(s: Source) {
    const next = !s.enabled;
    setPending(s.id);
    try {
      const res = await fetch(`/api/sources/${s.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.push("ok", `${s.id} ${next ? "enabled" : "disabled"}`);
      router.refresh();
    } catch (e) {
      toast.push("err", `Failed to update ${s.id}: ${e instanceof Error ? e.message : e}`);
    } finally {
      setPending(null);
    }
  }

  const enabled = sources.filter((s) => s.enabled).length;
  const unhealthy = sources.filter((s) => s.error_streak > 0).length;

  return (
    <Card>
      <CardHeader
        icon={<Rss className="h-4 w-4" />}
        title="Sources"
        subtitle={
          <span>
            {sources.length} registered · {enabled} enabled
            {unhealthy > 0 && <span className="text-amber-400"> · {unhealthy} with errors</span>}
          </span>
        }
      />

      {sources.length === 0 ? (
        <EmptyState
          icon={<Radio className="h-5 w-5" />}
          title="No sources registered"
          hint="Sources appear here once the platform registers them on startup."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[11px] uppercase tracking-[0.1em] text-faint">
                <th className="px-5 py-2.5 font-medium">Source</th>
                <th className="px-3 py-2.5 font-medium">Module</th>
                <th className="px-3 py-2.5 font-medium">Interval</th>
                <th className="px-3 py-2.5 font-medium">Last success</th>
                <th className="px-3 py-2.5 font-medium">Errors</th>
                <th className="px-5 py-2.5 text-right font-medium">State</th>
              </tr>
            </thead>
            <motion.tbody
              initial="hidden"
              animate="show"
              variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}
              className="divide-y divide-border/60"
            >
              {sources.map((s) => (
                <motion.tr
                  key={s.id}
                  variants={{
                    hidden: { opacity: 0, y: 8 },
                    show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] } },
                  }}
                  whileHover={{ backgroundColor: "rgba(255,255,255,0.02)" }}
                  className="transition-colors"
                >
                  <td className="px-5 py-3.5 font-mono text-[13px] text-fg">{s.id}</td>
                  <td className="px-3 py-3.5">
                    <Badge tone={MODULE_TONE[s.module] || "neutral"}>{s.module}</Badge>
                  </td>
                  <td className="px-3 py-3.5 tabular-nums text-muted">
                    {s.interval_minutes}m
                  </td>
                  <td className="px-3 py-3.5 text-muted">{relTime(s.last_success_at)}</td>
                  <td className="px-3 py-3.5">
                    {s.error_streak > 0 ? (
                      <Badge tone="amber">{s.error_streak}</Badge>
                    ) : (
                      <span className="text-faint">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex justify-end">
                      <Toggle
                        checked={s.enabled}
                        disabled={pending === s.id}
                        loading={pending === s.id}
                        onChange={() => toggle(s)}
                        labels={["on", "off"]}
                      />
                    </div>
                  </td>
                </motion.tr>
              ))}
            </motion.tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function SourcesError({ error }: { error: string }) {
  return (
    <Card>
      <CardHeader icon={<Rss className="h-4 w-4" />} title="Sources" />
      <ErrorState error={error} />
    </Card>
  );
}
