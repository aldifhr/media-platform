"use client";

import { motion } from "framer-motion";
import { Lock, Loader2 } from "lucide-react";
import { useState } from "react";

export default function LoginForm({ error }: { error: boolean }) {
  const [submitting, setSubmitting] = useState(false);

  return (
    <motion.form
      action="/api/login"
      method="post"
      onSubmit={() => setSubmitting(true)}
      initial={{ opacity: 0, y: 18, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
      className="relative w-full max-w-sm rounded-2xl border border-border bg-surface/70 p-7 shadow-[0_30px_60px_-30px_rgba(0,0,0,0.9)] backdrop-blur-xl"
    >
     
      <p className="mt-6 text-sm leading-relaxed text-muted text-center">
        Sign in to manage sources, manhwa config, and source health.
      </p>

      <label className="mt-5 block">
        <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.1em] text-faint">
          Password
        </span>
        <div className="relative">
          <Lock className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            autoFocus
            required
            className="w-full rounded-lg border border-border-strong bg-bg/60 py-2.5 pl-9 pr-3 text-sm text-fg outline-none transition-colors focus:border-faint focus:ring-2 focus:ring-border-strong/60"
          />
        </div>
      </label>

      {error && (
        <motion.p
          initial={{ opacity: 0, x: -6 }}
          animate={{ opacity: 1, x: 0 }}
          className="mt-3 flex items-center gap-1.5 text-xs text-red-400"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
            <path d="M12 8v5M12 16.5v.01" strokeLinecap="round" />
          </svg>
          Wrong password. Try again.
        </motion.p>
      )}

      <motion.button
        type="submit"
        disabled={submitting}
        whileTap={{ scale: 0.98 }}
        className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-fg px-3 py-2.5 text-sm font-semibold text-bg transition-colors hover:bg-white disabled:opacity-70"
      >
        {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        {submitting ? "Signing in…" : "Sign in"}
      </motion.button>
    </motion.form>
  );
}
