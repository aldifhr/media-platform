export const dynamic = "force-dynamic";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <form
        action="/api/login"
        method="post"
        className="w-full max-w-sm rounded-xl border border-neutral-800 bg-neutral-900/50 p-6"
      >
        <h1 className="text-lg font-semibold tracking-tight">media-platform · ops</h1>
        <p className="mt-1 text-xs text-neutral-500">Sign in to manage the bot config.</p>

        <label className="mt-6 block text-xs text-neutral-400">
          Password
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            autoFocus
            required
            className="mt-1 w-full rounded-md border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-500"
          />
        </label>

        {error ? (
          <p className="mt-3 font-mono text-xs text-red-400">Wrong password.</p>
        ) : null}

        <button
          type="submit"
          className="mt-5 w-full rounded-md bg-neutral-100 px-3 py-2 text-sm font-medium text-neutral-900 transition-colors hover:bg-white"
        >
          Sign in
        </button>
      </form>
    </main>
  );
}
