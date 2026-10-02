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
