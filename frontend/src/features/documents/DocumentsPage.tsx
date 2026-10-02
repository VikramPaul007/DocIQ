import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RecordView } from "@/features/records/RecordView";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { JobOut, RecordOut, Template, TemplateVersion, UploadResult } from "@/lib/types";

const TERMINAL = new Set(["REVIEW_REQUIRED", "COMPLETED", "FAILED"]);

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    QUEUED: "bg-slate-100 text-slate-700",
    PROCESSING: "bg-blue-100 text-blue-800",
    REVIEW_REQUIRED: "bg-amber-100 text-amber-800",
    COMPLETED: "bg-green-100 text-green-800",
    FAILED: "bg-red-100 text-red-800",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${styles[status] ?? ""}`}>{status}</span>
  );
}

export function DocumentsPage() {
  const { workspaceId = "" } = useParams();
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const canManage =
    !!me?.user.is_platform_admin ||
    me?.memberships.some((m) => m.workspace_id === workspaceId && m.role === "admin");

  const { data: templates } = useQuery({
    queryKey: ["workspace", workspaceId, "templates"],
    queryFn: () => api.get<Template[]>(`/workspaces/${workspaceId}/templates`),
  });
  const [templateId, setTemplateId] = useState("");
  const { data: versions } = useQuery({
    queryKey: ["template", templateId, "versions"],
    queryFn: () => api.get<TemplateVersion[]>(`/templates/${templateId}/versions`),
    enabled: !!templateId,
  });
  const publishedVersions = versions?.filter((v) => v.status === "published") ?? [];
  const [versionId, setVersionId] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [jobIds, setJobIds] = useState<string[]>(() => {
    const saved = sessionStorage.getItem(`dociq:jobs:${workspaceId}`);
    return saved ? JSON.parse(saved) : [];
  });
  const persistJobIds = (ids: string[]) => {
    setJobIds(ids);
    sessionStorage.setItem(`dociq:jobs:${workspaceId}`, JSON.stringify(ids));
  };

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<UploadResult>(
        `/workspaces/${workspaceId}/documents?template_version_id=${versionId}`,
        form
      );
    },
    onSuccess: (result) => {
      persistJobIds([result.job_id, ...jobIds]);
      void queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "jobs"] });
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : "Upload failed"),
  });

  const { data: jobs } = useQuery({
    queryKey: ["workspace", workspaceId, "jobs", jobIds],
    queryFn: async () => {
      const results = await Promise.all(jobIds.map((id) => api.get<JobOut>(`/jobs/${id}`)));
      return results;
    },
    enabled: jobIds.length > 0,
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.every((j) => TERMINAL.has(j.status)) ? false : 2000;
    },
  });

  const retry = useMutation({
    mutationFn: (jobId: string) => api.post<JobOut>(`/jobs/${jobId}/retry`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "jobs"] }),
  });

  const [expandedRecordId, setExpandedRecordId] = useState<string | null>(null);
  const { data: expandedRecord } = useQuery({
    queryKey: ["record", expandedRecordId],
    queryFn: () => api.get<RecordOut>(`/records/${expandedRecordId}`),
    enabled: !!expandedRecordId,
  });

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold text-slate-900">Documents</h1>

      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>Upload a document</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 pt-4">
            <div className="flex gap-3">
              <select
                className="h-9 flex-1 rounded-md border border-slate-300 bg-white px-3 text-sm"
                value={templateId}
                onChange={(e) => {
                  setTemplateId(e.target.value);
                  setVersionId("");
                }}
              >
                <option value="">Select a template…</option>
                {templates?.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              <select
                className="h-9 flex-1 rounded-md border border-slate-300 bg-white px-3 text-sm"
                value={versionId}
                onChange={(e) => setVersionId(e.target.value)}
                disabled={!templateId}
              >
                <option value="">Select a published version…</option>
                {publishedVersions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_no}
                  </option>
                ))}
              </select>
            </div>
            {templateId && publishedVersions.length === 0 && (
              <p className="text-sm text-amber-700">
                This template has no published version yet — publish one first.
              </p>
            )}
            <input
              type="file"
              accept="application/pdf"
              disabled={!versionId || upload.isPending}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  setError(null);
                  upload.mutate(file);
                }
              }}
            />
            {error && <p className="text-sm text-red-600">{error}</p>}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Recent uploads (this browser session)</CardTitle>
        </CardHeader>
        <CardContent>
          {!jobs || jobs.length === 0 ? (
            <p className="text-sm text-slate-500">No uploads yet.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-2 font-medium">Status</th>
                  <th className="py-2 font-medium">Stage</th>
                  <th className="py-2 font-medium">Error</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <Fragment key={j.id}>
                    <tr className="border-b border-slate-100">
                      <td className="py-2">
                        <StatusBadge status={j.status} />
                      </td>
                      <td className="py-2">{j.stage ?? "—"}</td>
                      <td className="max-w-xs truncate py-2 text-red-600" title={j.last_error ?? ""}>
                        {j.last_error ?? ""}
                      </td>
                      <td className="py-2 text-right">
                        {j.status === "FAILED" && canManage && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={retry.isPending}
                            onClick={() => retry.mutate(j.id)}
                          >
                            Retry
                          </Button>
                        )}
                        {j.record_id && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              setExpandedRecordId(expandedRecordId === j.record_id ? null : j.record_id)
                            }
                          >
                            {expandedRecordId === j.record_id ? "Hide" : "View"}
                          </Button>
                        )}
                      </td>
                    </tr>
                    {expandedRecordId === j.record_id && expandedRecord && (
                      <tr>
                        <td colSpan={4} className="bg-slate-50 p-4">
                          <RecordView record={expandedRecord} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
