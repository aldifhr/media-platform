"use client";

import { LogOut } from "lucide-react";

export default function LogoutButton() {
  return (
    <form action="/api/logout" method="post">
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-lg border border-[#26262b] bg-[#111113]/60 px-3 py-1.5 text-xs font-medium text-[#8b8b93] transition-colors hover:border-[#33333a] hover:bg-[#17171a] hover:text-[#ededf0]"
      >
        <LogOut className="h-3.5 w-3.5" />
        Sign out
      </button>
    </form>
  );
}
