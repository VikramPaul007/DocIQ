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
