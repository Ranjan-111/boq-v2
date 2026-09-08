import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <div className="grid min-h-full place-items-center px-4 text-center">
      <div>
        <p className="text-5xl font-bold text-ink-200">404</p>
        <h1 className="mt-3 text-lg font-semibold text-ink-900">Page not found</h1>
        <p className="mt-1 text-sm text-ink-500">The page you wanted does not exist.</p>
        <Link to="/projects" className="btn-secondary mt-6 inline-flex">
          Back to projects
        </Link>
      </div>
    </div>
  );
}
