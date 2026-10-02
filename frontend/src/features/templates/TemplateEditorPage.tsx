import { useEffect, useMemo, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Template, TemplateVersion, ValidateResult } from "@/lib/types";

const tabTrigger =
  "rounded-md px-3 py-1.5 text-sm font-medium text-slate-600 hover:text-slate-900 " +
  "data-[state=active]:bg-slate-900 data-[state=active]:text-white";

export function TemplateEditorPage() {
  const { workspaceId = "", templateId = "" } = useParams();
  const { me } = useAuth();
  const queryClient = useQueryClient();

  const canManage =
    !!me?.user.is_platform_admin ||
    me?.memberships.some((m) => m.workspace_id === workspaceId && m.role === "admin");

  const { data: template } = useQuery({
    queryKey: ["template", templateId],
    queryFn: () => api.get<Template>(`/templates/${templateId}`),
  });

  const versionsKey = ["template", templateId, "versions"];
  const { data: versions } = useQuery({
    queryKey: versionsKey,
    queryFn: () => api.get<TemplateVersion[]>(`/templates/${templateId}/versions`),
  });

  const [selectedVersionId, setSelectedVersionId] = useState<string>("");
  useEffect(() => {
    if (versions && versions.length > 0 && !selectedVersionId) {
      const draft = versions.find((v) => v.status === "draft");
      setSelectedVersionId((draft ?? versions[0]).id);
    }
  }, [versions, selectedVersionId]);

  const { data: version } = useQuery({
    queryKey: ["version", selectedVersionId],
    queryFn: () => api.get<TemplateVersion>(`/versions/${selectedVersionId}`),
    enabled: !!selectedVersionId,
  });

  const [systemPrompt, setSystemPrompt] = useState("");
  const [schemaText, setSchemaText] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (version) {
      setSystemPrompt(version.system_prompt);
      setSchemaText(JSON.stringify(version.json_schema, null, 2));
      setDirty(false);
    }
  }, [version]);

  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidateResult | null>(null);

  const invalidateEverything = () => {
    void queryClient.invalidateQueries({ queryKey: versionsKey });
    void queryClient.invalidateQueries({ queryKey: ["version", selectedVersionId] });
  };

  const save = useMutation({
    mutationFn: () => {
      let parsedSchema: unknown;
      try {
        parsedSchema = JSON.parse(schemaText);
      } catch {
        throw new Error("Schema is not valid JSON");
      }
      return api.put(`/versions/${selectedVersionId}`, {
        system_prompt: systemPrompt,
        json_schema: parsedSchema,
      });
    },
    onSuccess: () => {
      setDirty(false);
      invalidateEverything();
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : "Save failed"),
  });

  const validate = useMutation({
    mutationFn: () => api.post<ValidateResult>(`/versions/${selectedVersionId}/validate`),
    onSuccess: (result) => setValidation(result),
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : "Validation failed"),
  });

  const publish = useMutation({
    mutationFn: () => api.post<TemplateVersion>(`/versions/${selectedVersionId}/publish`),
    onSuccess: () => {
      invalidateEverything();
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : "Publish failed"),
  });

  const openDraft = useMutation({
    mutationFn: () => api.post<TemplateVersion>(`/templates/${templateId}/draft`),
    onSuccess: (draft) => {
      setSelectedVersionId(draft.id);
      invalidateEverything();
    },
  });

  const isDraft = version?.status === "draft";
  const hasOpenDraft = useMemo(() => versions?.some((v) => v.status === "draft") ?? false, [versions]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">{template?.name ?? "…"}</h1>
        <div className="flex items-center gap-3">
          <select
            className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm"
            value={selectedVersionId}
            onChange={(e) => setSelectedVersionId(e.target.value)}
          >
            {versions?.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version_no} — {v.status}
              </option>
            ))}
          </select>
          {canManage && !isDraft && !hasOpenDraft && (
            <Button variant="outline" onClick={() => openDraft.mutate()}>
              Edit (new draft)
            </Button>
          )}
        </div>
      </div>

      {!isDraft && version && (
        <p className="rounded-md bg-slate-100 px-3 py-2 text-sm text-slate-600">
          This version is <strong>{version.status}</strong> and read-only.
          {canManage && !hasOpenDraft && " Click \"Edit (new draft)\" to make changes."}
        </p>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}

      <Tabs.Root defaultValue="prompt">
        <Tabs.List className="flex gap-2 border-b border-slate-200 pb-2">
          <Tabs.Trigger value="prompt" className={tabTrigger}>
            Prompt
          </Tabs.Trigger>
          <Tabs.Trigger value="schema" className={tabTrigger}>
            Schema
          </Tabs.Trigger>
          <Tabs.Trigger value="publish" className={tabTrigger}>
            Publish
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="prompt" className="pt-4">
          <Card>
            <CardContent className="flex flex-col gap-3 pt-4">
              <label className="text-sm font-medium text-slate-700" htmlFor="prompt">
                System prompt
              </label>
              <textarea
                id="prompt"
                className="h-48 w-full rounded-md border border-slate-300 p-3 font-mono text-sm disabled:bg-slate-50"
                value={systemPrompt}
                disabled={!isDraft || !canManage}
                onChange={(e) => {
                  setSystemPrompt(e.target.value);
                  setDirty(true);
                }}
              />
              {isDraft && canManage && (
                <Button
                  className="self-start"
                  disabled={!dirty || save.isPending}
                  onClick={() => {
                    setError(null);
                    save.mutate();
                  }}
                >
                  Save
                </Button>
              )}
            </CardContent>
          </Card>
        </Tabs.Content>

        <Tabs.Content value="schema" className="pt-4">
          <Card>
            <CardContent className="flex flex-col gap-3 pt-4">
              <label className="text-sm font-medium text-slate-700" htmlFor="schema">
                JSON Schema
              </label>
              <textarea
                id="schema"
                spellCheck={false}
                className="h-80 w-full rounded-md border border-slate-300 p-3 font-mono text-sm disabled:bg-slate-50"
                value={schemaText}
                disabled={!isDraft || !canManage}
                onChange={(e) => {
                  setSchemaText(e.target.value);
                  setDirty(true);
                }}
              />
              {isDraft && canManage && (
                <div className="flex gap-2">
                  <Button
                    disabled={!dirty || save.isPending}
                    onClick={() => {
                      setError(null);
                      save.mutate();
                    }}
                  >
                    Save
                  </Button>
                  <Button
                    variant="outline"
                    disabled={validate.isPending}
                    onClick={() => {
                      setError(null);
                      validate.mutate();
                    }}
                  >
                    Validate
                  </Button>
                </div>
              )}
              {validation && (
                <div className={validation.valid ? "text-sm text-green-700" : "text-sm text-red-600"}>
                  {validation.valid ? (
                    "Schema and rules are valid."
                  ) : (
                    <ul className="list-disc pl-5">
                      {validation.errors.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </Tabs.Content>

        <Tabs.Content value="publish" className="pt-4">
          <Card>
            <CardContent className="flex flex-col gap-3 pt-4">
              {version?.agent_generated && !version.human_reviewed_by && (
                <p className="text-sm text-amber-700">
                  This configuration was AI-generated and must be reviewed before it can be
                  published.
                </p>
              )}
              <p className="text-sm text-slate-600">
                Publishing locks this version permanently. Any later edit opens a new draft.
              </p>
              {isDraft && canManage ? (
                <Button
                  disabled={publish.isPending}
                  onClick={() => {
                    setError(null);
                    publish.mutate();
                  }}
                  className="self-start"
                >
                  Publish v{version?.version_no}
                </Button>
              ) : (
                <p className="text-sm text-slate-500">Only a draft version can be published.</p>
              )}
            </CardContent>
          </Card>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}
