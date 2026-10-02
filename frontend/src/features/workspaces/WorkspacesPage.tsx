import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Workspace } from "@/lib/types";

export function WorkspacesPage() {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const { data: workspaces, isLoading } = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Workspace[]>("/workspaces"),
  });

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const createWorkspace = useMutation({
    mutationFn: (payload: { name: string }) => api.post<Workspace>("/workspaces", payload),
    onSuccess: () => {
      setShowCreate(false);
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not create workspace");
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Workspaces</h1>
        {me?.user.is_platform_admin && (
          <Button onClick={() => setShowCreate((v) => !v)}>
            {showCreate ? "Cancel" : "New workspace"}
          </Button>
        )}
      </div>

      {showCreate && (
        <Card>
          <CardContent className="flex items-end gap-3 pt-4">
            <div className="flex flex-1 flex-col gap-1.5">
              <label className="text-sm font-medium text-slate-700" htmlFor="ws-name">
                Workspace name
              </label>
              <Input
                id="ws-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. HR"
              />
            </div>
            <Button
              disabled={!name.trim() || createWorkspace.isPending}
              onClick={() => {
                setError(null);
                createWorkspace.mutate({ name: name.trim() });
              }}
            >
              Create
            </Button>
          </CardContent>
          {error && <p className="px-4 pb-4 text-sm text-red-600">{error}</p>}
        </Card>
      )}

      {isLoading && <p className="text-slate-500">Loading…</p>}

      <div className="grid gap-4 sm:grid-cols-2">
        {workspaces?.map((ws) => (
          <Link key={ws.id} to={`/workspaces/${ws.id}`}>
            <Card className="h-full transition-colors hover:border-slate-400">
              <CardHeader>
                <CardTitle>{ws.name}</CardTitle>
              </CardHeader>
              {ws.description && <CardContent className="pt-0 text-sm text-slate-600">{ws.description}</CardContent>}
            </Card>
          </Link>
        ))}
        {workspaces?.length === 0 && !isLoading && (
          <p className="text-slate-500">No workspaces yet.</p>
        )}
      </div>
    </div>
  );
}
