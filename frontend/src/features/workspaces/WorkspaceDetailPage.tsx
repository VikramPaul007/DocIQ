import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Member, Role, Workspace } from "@/lib/types";

export function WorkspaceDetailPage() {
  const { workspaceId = "" } = useParams();
  const { me } = useAuth();
  const queryClient = useQueryClient();

  const canManage =
    !!me?.user.is_platform_admin ||
    me?.memberships.some((m) => m.workspace_id === workspaceId && m.role === "admin");

  const { data: workspace } = useQuery({
    queryKey: ["workspace", workspaceId],
    queryFn: () => api.get<Workspace>(`/workspaces/${workspaceId}`),
  });
  const { data: members, isLoading } = useQuery({
    queryKey: ["workspace", workspaceId, "members"],
    queryFn: () => api.get<Member[]>(`/workspaces/${workspaceId}/members`),
  });

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("reviewer");
  const [error, setError] = useState<string | null>(null);

  const invalidateMembers = () =>
    queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "members"] });

  const addMember = useMutation({
    mutationFn: () => api.put<Member>(`/workspaces/${workspaceId}/members`, { email, role }),
    onSuccess: () => {
      setEmail("");
      void invalidateMembers();
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : "Could not add member"),
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) => api.delete(`/workspaces/${workspaceId}/members/${userId}`),
    onSuccess: () => void invalidateMembers(),
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : "Could not remove member"),
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">{workspace?.name ?? "…"}</h1>
        <div className="flex gap-4">
          <Link
            to={`/workspaces/${workspaceId}/templates`}
            className="text-sm font-medium text-slate-700 underline"
          >
            Templates →
          </Link>
          <Link
            to={`/workspaces/${workspaceId}/documents`}
            className="text-sm font-medium text-slate-700 underline"
          >
            Documents →
          </Link>
          <Link
            to={`/workspaces/${workspaceId}/review`}
            className="text-sm font-medium text-slate-700 underline"
          >
            Review queue →
          </Link>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Members</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {canManage && (
            <div className="flex items-end gap-3">
              <div className="flex flex-1 flex-col gap-1.5">
                <label className="text-sm font-medium text-slate-700" htmlFor="member-email">
                  Email
                </label>
                <Input
                  id="member-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="person@company.com"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-slate-700" htmlFor="member-role">
                  Role
                </label>
                <select
                  id="member-role"
                  className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm"
                  value={role}
                  onChange={(e) => setRole(e.target.value as Role)}
                >
                  <option value="admin">Admin</option>
                  <option value="reviewer">Reviewer</option>
                </select>
              </div>
              <Button
                disabled={!email.trim() || addMember.isPending}
                onClick={() => {
                  setError(null);
                  addMember.mutate();
                }}
              >
                Add
              </Button>
            </div>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}

          {isLoading ? (
            <p className="text-slate-500">Loading…</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-2 font-medium">Email</th>
                  <th className="py-2 font-medium">Role</th>
                  {canManage && <th className="py-2" />}
                </tr>
              </thead>
              <tbody>
                {members?.map((m) => (
                  <tr key={m.user_id} className="border-b border-slate-100">
                    <td className="py-2">{m.email}</td>
                    <td className="py-2 capitalize">{m.role}</td>
                    {canManage && (
                      <td className="py-2 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setError(null);
                            removeMember.mutate(m.user_id);
                          }}
                        >
                          Remove
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
