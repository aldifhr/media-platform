"use client";

import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Toggle({
  checked,
  disabled,
  loading,
  onChange,
  labels,
}: {
  checked: boolean;
  disabled?: boolean;
  loading?: boolean;
  onChange: (next: boolean) => void;
  labels?: [on: string, off: string];
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="group inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-200",
          checked ? "bg-emerald-500/80" : "bg-border-strong",
        )}
      >
        <motion.span
          layout
          transition={{ type: "spring", stiffness: 600, damping: 32 }}
          className={cn(
            "inline-block h-3.5 w-3.5 rounded-full bg-white shadow",
            checked ? "ml-[18px]" : "ml-[3px]",
          )}
        />
      </span>
      {loading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin text-muted" />
      ) : (
        labels && (
          <span className="text-xs font-medium text-muted">
            {checked ? labels[0] : labels[1]}
          </span>
        )
      )}
    </button>
  );
}
