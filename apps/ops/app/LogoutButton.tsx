"use client";

export default function LogoutButton() {
  return (
    <form action="/api/logout" method="post">
      <button
        type="submit"
        className="rounded-md border border-neutral-700 px-3 py-1 text-xs text-neutral-300 transition-colors hover:bg-neutral-800"
      >
        Sign out
      </button>
    </form>
  );
}
