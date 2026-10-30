"use client";

import { Server } from "lucide-react";
import type { MediaConfig } from "@/lib/api";
import { Card, CardHeader } from "./components/ui";

type Field = { k: string; label: string };

const SOURCES: { key: keyof MediaConfig; label: string; fields: Field[] }[] = [
  {
    key: "pahe",
    label: "Pahe",
    fields: [
      { k: "thread_2026", label: "2026 thread" },
      { k: "thread_2025", label: "2025 thread" },
      { k: "thread_horror", label: "Horror thread" },
      { k: "fallback_channel", label: "Fallback channel" },
    ],
  },
  { key: "ovagames", label: "Ova Games", fields: [{ k: "thread", label: "Thread" }] },
  {
    key: "skidrow",
    label: "Skidrow & Reloaded",
    fields: [{ k: "thread", label: "Thread" }],
  },
  { key: "game3rb", label: "Game3rb", fields: [{ k: "thread", label: "Thread" }] },
  {
    key: "drakorasia",
    label: "Drakorasia",
    fields: [
      { k: "korea", label: "Korea thread" },
      { k: "china", label: "China thread" },
    ],
  },
];

export default function MediaConfigPanel({ config }: { config: MediaConfig | null }) {
  return (
    <Card>
      <CardHeader
        icon={<Server className="h-4 w-4" />}
        title="Media delivery"
        subtitle="Discord threads per media source · read from deploy env"
      />
      <div className="divide-y divide-border/60">
        {SOURCES.map((s) => {
          const vals = (config?.[s.key] ?? {}) as Record<string, string>;
          return (
            <div key={s.key} className="px-5 py-4">
              <div className="mb-2.5 font-mono text-[13px] text-fg">{s.label}</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {s.fields.map((f) => {
                  const v = (vals[f.k] || "").trim();
                  return (
                    <div
                      key={f.k}
                      className="flex items-center justify-between rounded-lg border border-border bg-bg/40 px-3 py-2"
                    >
                      <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-faint">
                        {f.label}
                      </span>
                      <code className="font-mono text-[11px] text-fg">{v || "—"}</code>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
