"use client";

import { motion } from "framer-motion";
import { Radio } from "lucide-react";
import type { Route } from "@/lib/api";
import { Badge, Card, CardHeader, EmptyState, ErrorState } from "./components/ui";

export default function RoutesTable({
  routes,
  error,
}: {
  routes: Route[];
  error: string | null;
}) {
  return (
    <Card>
      <CardHeader
        icon={<Radio className="h-4 w-4" />}
        title="Notification routes"
        subtitle={`${routes.length} rules`}
      />
      {error ? (
        <ErrorState error={error} />
      ) : routes.length === 0 ? (
        <EmptyState
          icon={<Radio className="h-5 w-5" />}
          title="No routes configured"
          hint="Routes map module events to a channel or thread."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-[#26262b] text-left text-[11px] uppercase tracking-wider text-[#5c5c64]">
                <th className="px-5 py-2.5 font-medium">Priority</th>
                <th className="px-3 py-2.5 font-medium">Module</th>
                <th className="px-3 py-2.5 font-medium">Event</th>
                <th className="px-3 py-2.5 font-medium">Target</th>
                <th className="px-5 py-2.5 text-right font-medium">State</th>
              </tr>
            </thead>
            <motion.tbody
              initial="hidden"
              animate="show"
              variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}
              className="divide-y divide-[#26262b]/60"
            >
              {routes.map((r) => (
                <motion.tr
                  key={r.id}
                  variants={{
                    hidden: { opacity: 0, y: 8 },
                    show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] } },
                  }}
                  whileHover={{ backgroundColor: "rgba(255,255,255,0.02)" }}
                  className="transition-colors"
                >
                  <td className="px-5 py-3 tabular-nums text-[#8b8b93]">p{r.priority}</td>
                  <td className="px-3 py-3">
                    <Badge tone={r.module === "manhwa" ? "violet" : "blue"}>{r.module}</Badge>
                  </td>
                  <td className="px-3 py-3 font-mono text-[13px] text-[#ededf0]">{r.event_type}</td>
                  <td className="px-3 py-3 font-mono text-xs text-[#8b8b93]">
                    {r.thread_id ? `thread ${r.thread_id}` : `channel ${r.channel_id}`}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <Badge tone={r.enabled ? "green" : "neutral"}>{r.enabled ? "on" : "off"}</Badge>
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
