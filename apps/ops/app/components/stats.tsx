"use client";

import { motion } from "framer-motion";
import { AnimatedNumber } from "./motion";
import { Dot, type Tone } from "./ui";
import { cn } from "@/lib/utils";

export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
  icon,
  delay = 0,
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: Tone;
  icon?: React.ReactNode;
  delay?: number;
}) {
  const accent: Record<Tone, string> = {
    neutral: "text-[#ededf0]",
    green: "text-emerald-300",
    amber: "text-amber-300",
    red: "text-red-300",
    blue: "text-sky-300",
    violet: "text-violet-300",
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1], delay }}
      whileHover={{ y: -2 }}
      className="group relative overflow-hidden rounded-2xl border border-[#26262b] bg-[#111113]/80 px-4 py-3.5 transition-colors hover:border-[#33333a]"
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-[#8b8b93]">
          {icon}
          {label}
        </span>
        {tone !== "neutral" && <Dot tone={tone} pulse={tone === "green" || tone === "red"} />}
      </div>
      <div className={cn("mt-1.5 text-2xl font-semibold tabular-nums", accent[tone])}>
        <AnimatedNumber value={value} />
      </div>
      {hint && <div className="mt-0.5 truncate text-[11px] text-[#5c5c64]">{hint}</div>}
      <span className="pointer-events-none absolute -right-8 -top-8 h-20 w-20 rounded-full bg-white/[0.02] opacity-0 blur-xl transition-opacity group-hover:opacity-100" />
    </motion.div>
  );
}
