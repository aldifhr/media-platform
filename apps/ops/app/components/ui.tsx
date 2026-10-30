import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ Card */

export function Card({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLElement>) {
  return (
    <section
      className={cn(
        "overflow-hidden rounded-2xl border border-[#26262b] bg-[#111113]/80 shadow-[0_1px_0_0_rgba(255,255,255,0.03)_inset,0_20px_40px_-24px_rgba(0,0,0,0.8)] backdrop-blur-sm",
        className,
      )}
      {...props}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  subtitle,
  icon,
  action,
  className,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <header
      className={cn(
        "flex items-center justify-between gap-4 border-b border-[#26262b] px-5 py-3.5",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-3">
        {icon && (
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[#26262b] bg-[#17171a] text-[#8b8b93]">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-[#ededf0]">{title}</h2>
          {subtitle && (
            <p className="mt-0.5 truncate text-xs text-[#8b8b93]">{subtitle}</p>
          )}
        </div>
      </div>
      {action}
    </header>
  );
}

/* ----------------------------------------------------------------- Badge */

const badge = cva(
  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "bg-[#17171a] text-[#8b8b93] ring-[#26262b]",
        green: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/25",
        amber: "bg-amber-500/10 text-amber-300 ring-amber-500/25",
        red: "bg-red-500/10 text-red-300 ring-red-500/25",
        blue: "bg-sky-500/10 text-sky-300 ring-sky-500/25",
        violet: "bg-violet-500/10 text-violet-300 ring-violet-500/25",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type Tone = NonNullable<VariantProps<typeof badge>["tone"]>;

export function Badge({
  tone,
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
}) {
  return <span className={cn(badge({ tone }), className)}>{children}</span>;
}

/* ------------------------------------------------------------------- Dot */

const DOT: Record<Tone, string> = {
  neutral: "bg-[#5c5c64]",
  green: "bg-emerald-400",
  amber: "bg-amber-400",
  red: "bg-red-400",
  blue: "bg-sky-400",
  violet: "bg-violet-400",
};

export function Dot({ tone = "neutral", pulse }: { tone?: Tone; pulse?: boolean }) {
  return (
    <span className="relative flex h-2 w-2">
      {pulse && (
        <span
          className={cn(
            "absolute inline-flex h-full w-full animate-ping rounded-full opacity-60",
            DOT[tone],
          )}
        />
      )}
      <span className={cn("relative inline-flex h-2 w-2 rounded-full", DOT[tone])} />
    </span>
  );
}

/* --------------------------------------------------------------- Skeleton */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton rounded-md", className)} />;
}

/* ------------------------------------------------------------ Empty/Error */

export function EmptyState({
  title,
  hint,
  icon,
  action,
}: {
  title: string;
  hint?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-dashed border-[#33333a] text-[#5c5c64]">
        {icon ?? (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M3 7h18M3 12h18M3 17h10" strokeLinecap="round" />
          </svg>
        )}
      </div>
      <p className="text-sm font-medium text-[#ededf0]">{title}</p>
      {hint && <p className="max-w-xs text-xs text-[#8b8b93]">{hint}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error }: { error: string }) {
  return (
    <div className="flex items-start gap-3 px-5 py-4">
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-red-500/15 text-red-400">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
          <path d="M12 8v5M12 16.5v.01" strokeLinecap="round" />
        </svg>
      </span>
      <div className="min-w-0">
        <p className="text-xs font-medium text-red-300">Couldn&apos;t load this section</p>
        <p className="mt-0.5 break-words font-mono text-[11px] text-red-400/70">{error}</p>
      </div>
    </div>
  );
}
