"""Pydantic request/response models for the auth, user and workspace routes.
Template/rule/document/review schemas live alongside their routers as those
land (see build order in docs/technical-implementation-plan.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Role = Literal["admin", "reviewer"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    is_platform_admin: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    is_platform_admin: bool = False


class MembershipOut(BaseModel):
    workspace_id: uuid.UUID
    role: Role


class MeResponse(BaseModel):
    user: UserOut
    csrf_token: str
    memberships: list[MembershipOut]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MemberAdd(BaseModel):
    email: EmailStr
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: Role
    created_at: datetime
