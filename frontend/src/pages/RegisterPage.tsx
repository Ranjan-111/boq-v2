import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../stores/auth";
import { ApiError } from "../lib/apiClient";

export default function RegisterPage() {
  const register = useAuth((s) => s.register);
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await register(email, password, displayName);
      navigate("/projects", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Sign-up failed. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-lg bg-accent-600 text-sm font-bold text-white">
            BQ
          </div>
          <h1 className="text-lg font-semibold text-ink-900">Create your account</h1>
          <p className="mt-1 text-sm text-ink-500">
            Takeoff and bill of quantities workspace
          </p>
        </div>
        <form onSubmit={onSubmit} className="card space-y-4 p-6">
          <div>
            <label className="label" htmlFor="display-name">
              Display name
            </label>
            <input
              id="display-name"
              type="text"
              className="input"
              autoComplete="name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div>
            <label className="label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              className="input"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              className="input"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
            />
            <p className="mt-1 text-xs text-ink-400">At least 8 characters.</p>
          </div>
          {error ? (
            <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          ) : null}
          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy ? "Creating account…" : "Create account"}
          </button>
          <p className="text-center text-sm text-ink-500">
            Already have an account?{" "}
            <Link to="/login" className="font-medium text-accent-700 hover:underline">
              Sign in
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
