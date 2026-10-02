import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "@/lib/auth-context";

export function RequireAuth() {
  const { me, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="flex min-h-screen items-center justify-center text-slate-500">Loading…</div>;
  }
  if (!me) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}
