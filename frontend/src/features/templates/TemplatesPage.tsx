import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Template } from "@/lib/types";

export function TemplatesPage() {
  const { workspaceId = "" } = useParams();
  const { me } = useAuth();
  const queryClient = useQueryClient();

  const canManage =
    !!me?.user.is_platform_admin ||
    me?.memberships.some((m) => m.workspace_id === workspaceId && m.role === "admin");

  const { data: templates, isLoading } = useQuery({
    queryKey: ["workspace", workspaceId, "templates"],
    queryFn: () => api.get<Template[]>(`/workspaces/${workspaceId}/templates`),
  });

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [documentType, setDocumentType] = useState("pdf");
  const [error, setError] = useState<string | null>(null);

  const createTemplate = useMutation({
    mutationFn: () =>
      api.post<Template>(`/workspaces/${workspaceId}/templates`, {
        name,
        purpose,
        document_type: documentType,
      }),
    onSuccess: () => {
      setShowCreate(false);
      setName("");
      setPurpose("");
      void queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "templates"] });
    },
    onError: (err: unknown) =>
      setError(err instanceof ApiError ? err.message : "Could not create template"),
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Templates</h1>
        {canManage && (
          <Button onClick={() => setShowCreate((v) => !v)}>
            {showCreate ? "Cancel" : "New template"}
          </Button>
        )}
      </div>

      {showCreate && (
        <Card>
          <CardContent className="flex flex-col gap-3 pt-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="t-name">Name</Label>
              <Input id="t-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="t-purpose">Purpose (what should be extracted?)</Label>
              <Input
                id="t-purpose"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                placeholder="Extract employee identity and mandatory onboarding documents"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="t-doctype">Document type</Label>
              <Input
                id="t-doctype"
                value={documentType}
                onChange={(e) => setDocumentType(e.target.value)}
              />
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <Button
              disabled={!name.trim() || !purpose.trim() || createTemplate.isPending}
              onClick={() => {
                setError(null);
                createTemplate.mutate();
              }}
              className="self-start"
            >
              Create
            </Button>
          </CardContent>
        </Card>
      )}

      {isLoading && <p className="text-slate-500">Loading…</p>}

      <div className="grid gap-4 sm:grid-cols-2">
        {templates?.map((t) => (
          <Link key={t.id} to={`/workspaces/${workspaceId}/templates/${t.id}`}>
            <Card className="h-full transition-colors hover:border-slate-400">
              <CardHeader>
                <CardTitle>{t.name}</CardTitle>
              </CardHeader>
              <CardContent className="pt-0 text-sm text-slate-600">{t.purpose}</CardContent>
            </Card>
          </Link>
        ))}
        {templates?.length === 0 && !isLoading && <p className="text-slate-500">No templates yet.</p>}
      </div>
    </div>
  );
}
