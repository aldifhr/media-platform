"use client";

import { LogOut } from "lucide-react";

export default function LogoutButton() {
  return (
    <form action="/api/logout" method="post">
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface/60 px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-border-strong hover:bg-surface-2 hover:text-fg"
      >
        <LogOut className="h-3.5 w-3.5" />
        Sign out
      </button>
    </form>
  );
}
