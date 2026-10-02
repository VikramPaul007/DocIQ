/** Mirrors backend/app/schemas.py. Keep in sync until gen:api-types takes over. */

export type Role = "admin" | "reviewer";

export interface User {
  id: string;
  email: string;
  is_platform_admin: boolean;
  is_active: boolean;
  created_at: string;
}

export interface Membership {
  workspace_id: string;
  role: Role;
}

export interface Me {
  user: User;
  csrf_token: string;
  memberships: Membership[];
}

export interface Workspace {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
}

export interface Member {
  user_id: string;
  email: string;
  role: Role;
  created_at: string;
}

export interface Template {
  id: string;
  workspace_id: string;
  name: string;
  purpose: string;
  document_type: string;
  created_at: string;
}

export type VersionStatus = "draft" | "published" | "archived";

export interface TemplateVersion {
  id: string;
  template_id: string;
  version_no: number;
  status: VersionStatus;
  system_prompt: string;
  json_schema: Record<string, unknown>;
  rules: unknown[];
  scoring: Record<string, unknown>;
  agent_generated: boolean;
  human_reviewed_by: string | null;
  human_reviewed_at: string | null;
  content_hash: string | null;
  published_by: string | null;
  published_at: string | null;
  created_at: string;
}

export interface ValidateResult {
  valid: boolean;
  errors: string[];
}

export interface DocumentMeta {
  id: string;
  workspace_id: string;
  template_version_id: string;
  sha256: string;
  size: number;
  page_count: number | null;
  original_filename: string;
  created_at: string;
}

export type JobStatusValue = "QUEUED" | "PROCESSING" | "REVIEW_REQUIRED" | "COMPLETED" | "FAILED";

export interface JobOut {
  id: string;
  document_id: string;
  kind: "runtime" | "test";
  status: JobStatusValue;
  stage: string | null;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  error_class: string | null;
  created_at: string;
  updated_at: string;
  record_id: string | null;
}

export interface UploadResult {
  document_id: string;
  job_id: string;
}

export interface OcrPage {
  page: number;
  method: "text_layer" | "ocr";
  text: string;
  confidence: number | null;
}

export interface RuleResultOut {
  rule_id: string;
  category: string;
  passed: boolean;
  message: string;
  actual: unknown;
}

export interface Contributor {
  rule_id: string;
  category: string;
  weight: string;
  message: string;
}

export interface ReviewQueueEntry {
  job_id: string;
  document_id: string;
  record_id: string;
  template_version_id: string;
  original_filename: string;
  risk_level: "low" | "medium" | "high" | null;
  score: string | null;
  summary: string | null;
  created_at: string;
}

export type ReviewAction = "approve" | "reject" | "request_correction";

export interface ReviewOut {
  id: string;
  record_id: string;
  reviewer_id: string;
  action: ReviewAction;
  comment: string | null;
  created_at: string;
}

export interface TimelineEntry {
  at: string;
  actor_email: string | null;
  action: string;
  details: Record<string, unknown> | null;
}

export interface RecordOut {
  id: string;
  job_id: string;
  document_id: string;
  template_version_id: string;
  ocr: { pages: OcrPage[] } | null;
  extraction: Record<string, unknown> | null;
  extraction_valid: boolean | null;
  validation_errors: string[] | null;
  rule_results: RuleResultOut[] | null;
  score: string | null;
  risk_level: "low" | "medium" | "high" | null;
  contributors: Contributor[] | null;
  summary: string | null;
  evaluated_at: string | null;
  created_at: string;
}
