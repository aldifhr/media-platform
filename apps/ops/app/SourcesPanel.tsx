"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Source } from "@/lib/api";

export default function SourcesPanel({ sources }: { sources: Source[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);

  async function toggle(s: Source) {
    setBusy(s.id);
    try {
      await fetch(`/api/sources/${s.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !s.enabled }),
      });
      router.refresh();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="rounded-xl border border-neutral-800 bg-neutral-900/50">
      <h2 className="border-b border-neutral-800 px-4 py-3 text-sm font-medium text-neutral-300">
        Sources
      </h2>
      <ul className="divide-y divide-neutral-800">
        {sources.map((s) => (
          <li key={s.id} className="flex items-center justify-between px-4 py-3">
            <div>
              <div className="font-mono text-sm text-neutral-100">{s.id}</div>
              <div className="text-xs text-neutral-500">
                {s.module} · {s.category} · every {s.interval_minutes}m · errors {s.error_streak}
              </div>
            </div>
            <button
              onClick={() => toggle(s)}
              disabled={busy === s.id}
              className={
                "rounded-md px-3 py-1 text-xs font-medium transition-colors " +
                (s.enabled
                  ? "bg-emerald-600/20 text-emerald-300 hover:bg-emerald-600/30"
                  : "bg-neutral-700/40 text-neutral-400 hover:bg-neutral-700/60")
              }
            >
              {s.enabled ? "enabled" : "disabled"}
            </button>
          </li>
        ))}
        {sources.length === 0 && (
          <li className="px-4 py-3 text-sm text-neutral-500">No sources registered.</li>
        )}
      </ul>
    </section>
  );
}
