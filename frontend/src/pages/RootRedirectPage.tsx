import { Navigate } from "react-router-dom";
import { useAuth } from "../stores/auth";

/** Root entry: logged-in users land on their projects, others on /login. */
export default function RootRedirectPage() {
  const token = useAuth((s) => s.token);
  return <Navigate to={token ? "/projects" : "/login"} replace />;
}
