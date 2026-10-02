import { Outlet, Link, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";

export function AppShell() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();

  const onLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <Link to="/workspaces" className="text-lg font-semibold text-slate-900">
          DocIQ
        </Link>
        <div className="flex items-center gap-4 text-sm text-slate-600">
          <span>{me?.user.email}</span>
          <Button variant="outline" size="sm" onClick={onLogout}>
            Sign out
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
