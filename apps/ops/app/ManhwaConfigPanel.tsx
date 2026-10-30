"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { ManhwaConfig, ManhwaHealth } from "@/lib/api";

const INPUT =
  "mt-1 w-full rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1 font-mono text-xs text-neutral-100 outline-none focus:border-neutral-500";

function GuildForm({
  g,
  busy,
  onSave,
}: {
  g: ManhwaConfig;
  busy: boolean;
  onSave: (g: ManhwaConfig, raw: string, wl: string, legacy: string) => void;
}) {
  const [raw, setRaw] = useState(g.raw_channel_id || "");
  const [wl, setWl] = useState(g.whitelist_channel_id || "");
  const [legacy, setLegacy] = useState(g.channel_id || "");
  return (
    <div className="space-y-2 px-4 py-3">
      <div className="font-mono text-xs text-neutral-400">guild {g.guild_id}</div>
      <label className="block text-xs text-neutral-500">
        RAW channel
        <input value={raw} onChange={(e) => setRaw(e.target.value)} className={INPUT} />
      </label>
      <label className="block text-xs text-neutral-500">
        Whitelist channel
        <input value={wl} onChange={(e) => setWl(e.target.value)} className={INPUT} />
      </label>
      <label className="block text-xs text-neutral-500">
        Legacy channel
        <input value={legacy} onChange={(e) => setLegacy(e.target.value)} className={INPUT} />
      </label>
      <button
        onClick={() => onSave(g, raw, wl, legacy)}
        disabled={busy}
        className="rounded-md bg-neutral-700/50 px-3 py-1 text-xs font-medium text-neutral-200 transition-colors hover:bg-neutral-600/60 disabled:opacity-50"
      >
        {busy ? "saving…" : "Save channels"}
      </button>
    </div>
  );
}

export default function ManhwaConfigPanel({
  config,
  health,
}: {
  config: ManhwaConfig[];
  health: ManhwaHealth[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function save(g: ManhwaConfig, raw: string, wl: string, legacy: string) {
    setBusy(true);
    try {
      await fetch(`/api/manhwa/config/${g.guild_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          raw_channel_id: raw,
          whitelist_channel_id: wl,
          channel_id: legacy,
        }),
      });
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-neutral-800 bg-neutral-900/50">
      <h2 className="border-b border-neutral-800 px-4 py-3 text-sm font-medium text-neutral-300">
        Manhwa bot config
      </h2>
      {config.length === 0 ? (
        <div className="px-4 py-3 text-sm text-neutral-500">No manhwa guild configured.</div>
      ) : (
        config.map((g) => (
          <GuildForm key={g.guild_id} g={g} busy={busy} onSave={save} />
        ))
      )}
      <div className="border-t border-neutral-800 px-4 py-3">
        <div className="mb-2 text-xs text-neutral-400">Source health</div>
        {health.length === 0 ? (
          <span className="text-xs text-neutral-500">No health data.</span>
        ) : (
          <ul className="space-y-1 font-mono text-xs">
            {health.map((h) => (
              <li key={h.source} className="text-neutral-300">
                {h.source}:{" "}
                <span className={h.status === "HEALTHY" ? "text-emerald-400" : "text-amber-400"}>
                  {h.status}
                </span>
                {h.response_time_ms != null && ` · ${h.response_time_ms}ms`}
                {h.consecutive_failures > 0 && ` · fails ${h.consecutive_failures}`}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
