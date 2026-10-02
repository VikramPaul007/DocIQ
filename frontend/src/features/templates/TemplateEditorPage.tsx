import { useEffect, useMemo, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RecordView } from "@/features/records/RecordView";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { fieldPaths } from "@/lib/schema-fields";
import type { JobOut, RecordOut, Template, TemplateVersion, UploadResult, ValidateResult } from "@/lib/types";

const TERMINAL_JOB_STATUSES = new Set(["REVIEW_REQUIRED", "COMPLETED", "FAILED"]);

const RULE_CATEGORIES = [
  "completeness",
  "validation",
  "eligibility",
  "risk",
  "compliance",
  "business",
] as const;

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
  const [rulesText, setRulesText] = useState("");
  const [scoringText, setScoringText] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (version) {
      setSystemPrompt(version.system_prompt);
      setSchemaText(JSON.stringify(version.json_schema, null, 2));
      setRulesText(JSON.stringify(version.rules, null, 2));
      setScoringText(JSON.stringify(version.scoring, null, 2));
      setDirty(false);
    }
  }, [version]);

  const availableFields = useMemo(() => {
    try {
      return fieldPaths(JSON.parse(schemaText));
    } catch {
      return {};
    }
  }, [schemaText]);

  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidateResult | null>(null);

  const invalidateEverything = () => {
    void queryClient.invalidateQueries({ queryKey: versionsKey });
    void queryClient.invalidateQueries({ queryKey: ["version", selectedVersionId] });
  };

  const save = useMutation({
    mutationFn: () => {
      const parse = (label: string, text: string): unknown => {
        try {
          return JSON.parse(text);
        } catch {
          throw new Error(`${label} is not valid JSON`);
        }
      };
      return api.put(`/versions/${selectedVersionId}`, {
        system_prompt: systemPrompt,
        json_schema: parse("Schema", schemaText),
        rules: parse("Rules", rulesText),
        scoring: parse("Scoring", scoringText),
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

  const generateConfig = useMutation({
    mutationFn: () => api.post<TemplateVersion>(`/templates/${templateId}/generate`),
    onSuccess: (draft) => {
      setSystemPrompt(draft.system_prompt);
      setSchemaText(JSON.stringify(draft.json_schema, null, 2));
      setDirty(false);
      setValidation(null);
      invalidateEverything();
    },
    onError: (err: unknown) =>
      setError(err instanceof ApiError ? err.message : "Could not generate a configuration"),
  });

  const isDraft = version?.status === "draft";
  const hasOpenDraft = useMemo(() => versions?.some((v) => v.status === "draft") ?? false, [versions]);

  const [testJobId, setTestJobId] = useState<string | null>(null);
  const runTest = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<UploadResult>(`/versions/${selectedVersionId}/test`, form);
    },
    onSuccess: (result) => setTestJobId(result.job_id),
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : "Upload failed"),
  });

  const { data: testJob } = useQuery({
    queryKey: ["job", testJobId],
    queryFn: () => api.get<JobOut>(`/jobs/${testJobId}`),
    enabled: !!testJobId,
    refetchInterval: (query) =>
      query.state.data && TERMINAL_JOB_STATUSES.has(query.state.data.status) ? false : 1500,
  });

  const { data: testRecord } = useQuery({
    queryKey: ["record", testJob?.record_id],
    queryFn: () => api.get<RecordOut>(`/records/${testJob?.record_id}`),
    enabled: !!testJob?.record_id,
  });

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

      <Tabs.Root defaultValue="generate">
        <Tabs.List className="flex gap-2 border-b border-slate-200 pb-2">
          <Tabs.Trigger value="generate" className={tabTrigger}>
            Generate
          </Tabs.Trigger>
          <Tabs.Trigger value="prompt" className={tabTrigger}>
            Prompt
          </Tabs.Trigger>
          <Tabs.Trigger value="schema" className={tabTrigger}>
            Schema
          </Tabs.Trigger>
          <Tabs.Trigger value="rules" className={tabTrigger}>
            Rules
          </Tabs.Trigger>
          <Tabs.Trigger value="scoring" className={tabTrigger}>
            Scoring
          </Tabs.Trigger>
          <Tabs.Trigger value="test" className={tabTrigger}>
            Test
          </Tabs.Trigger>
          <Tabs.Trigger value="publish" className={tabTrigger}>
            Publish
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="generate" className="pt-4">
          <Card>
            <CardContent className="flex flex-col gap-3 pt-4">
              <p className="text-sm text-slate-600">
                Generates a first-draft system prompt and JSON schema from this template&rsquo;s
                purpose (&ldquo;{template?.purpose}&rdquo;). This overwrites the current
                draft&rsquo;s Prompt and Schema tabs &mdash; review and edit them, then mark
                reviewed before publishing.
              </p>
              {isDraft && canManage ? (
                <Button
                  className="self-start"
                  disabled={generateConfig.isPending}
                  onClick={() => {
                    setError(null);
                    generateConfig.mutate();
                  }}
                >
                  {generateConfig.isPending ? "Generating…" : "Generate"}
                </Button>
              ) : (
                <p className="text-sm text-slate-500">Open a draft to generate a configuration.</p>
              )}
            </CardContent>
          </Card>
        </Tabs.Content>

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

        <Tabs.Content value="rules" className="pt-4">
          <Card>
            <CardContent className="flex flex-col gap-3 pt-4">
              <p className="text-xs text-slate-500">
                Categories: {RULE_CATEGORIES.join(", ")}. Each rule:{" "}
                <code>{"{id, category, name, message, weight, condition}"}</code>. A condition is a
                leaf <code>{"{field, op, value?}"}</code> or a nested{" "}
                <code>{"{all:[...]} / {any:[...]} / {not: ...}"}</code>.
              </p>
              {Object.keys(availableFields).length > 0 && (
                <div className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">
                  <strong>Available fields:</strong>{" "}
                  {Object.entries(availableFields)
                    .map(([path, type]) => `${path} (${type})`)
                    .join(", ")}
                </div>
              )}
              <label className="text-sm font-medium text-slate-700" htmlFor="rules">
                Rules (JSON array)
              </label>
              <textarea
                id="rules"
                spellCheck={false}
                className="h-80 w-full rounded-md border border-slate-300 p-3 font-mono text-sm disabled:bg-slate-50"
                value={rulesText}
                disabled={!isDraft || !canManage}
                onChange={(e) => {
                  setRulesText(e.target.value);
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
            </CardContent>
          </Card>
        </Tabs.Content>

        <Tabs.Content value="scoring" className="pt-4">
          <Card>
            <CardContent className="flex flex-col gap-3 pt-4">
              <p className="text-xs text-slate-500">
                <code>{"{weights: {rule_id: number}, thresholds: {low, medium, high}}"}</code>.
                Score is the sum of weights of rules that <em>fail</em>; risk is the highest
                threshold the score reaches. Optional — leave as <code>{"{}"}</code> for an
                unscored template.
              </p>
              <label className="text-sm font-medium text-slate-700" htmlFor="scoring">
                Scoring config (JSON)
              </label>
              <textarea
                id="scoring"
                spellCheck={false}
                className="h-48 w-full rounded-md border border-slate-300 p-3 font-mono text-sm disabled:bg-slate-50"
                value={scoringText}
                disabled={!isDraft || !canManage}
                onChange={(e) => {
                  setScoringText(e.target.value);
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
                    "Schema, rules and scoring are all valid."
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

        <Tabs.Content value="test" className="pt-4">
          <Card>
            <CardContent className="flex flex-col gap-4 pt-4">
              <p className="text-sm text-slate-600">
                Run a sample PDF through this version (v{version?.version_no}, {version?.status}) and
                see every intermediate output — OCR, extraction, validation, rules, score, risk, and
                summary. This never affects the reviewer queue.
              </p>
              {canManage && (
                <input
                  type="file"
                  accept="application/pdf"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      setError(null);
                      setTestJobId(null);
                      runTest.mutate(file);
                    }
                  }}
                />
              )}
              {runTest.isPending && <p className="text-sm text-slate-500">Uploading…</p>}
              {testJob && !TERMINAL_JOB_STATUSES.has(testJob.status) && (
                <p className="text-sm text-slate-500">
                  Processing… ({testJob.status}
                  {testJob.stage ? ` — ${testJob.stage}` : ""})
                </p>
              )}
              {testJob?.status === "FAILED" && (
                <p className="text-sm text-red-600">Failed: {testJob.last_error}</p>
              )}
              {testRecord && <RecordView record={testRecord} />}
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
