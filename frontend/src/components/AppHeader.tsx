import { useAuth } from "../stores/auth";

/** Top application bar: product name + signed-in user + sign out. */
export default function AppHeader() {
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  return (
    <header className="border-b border-ink-200 bg-white">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <div className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent-600 text-xs font-bold text-white">
            BQ
          </span>
          <span className="text-sm font-semibold tracking-tight text-ink-900">
            boq-v2
          </span>
          <span className="hidden text-xs text-ink-400 sm:inline">
            takeoff &amp; bill of quantities
          </span>
        </div>
        {user ? (
          <div className="flex items-center gap-3">
            <span className="text-sm text-ink-600">{user.display_name}</span>
            <button className="btn-secondary !py-1 !text-xs" onClick={logout}>
              Sign out
            </button>
          </div>
        ) : null}
      </div>
    </header>
  );
}
