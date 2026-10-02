import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { LoginPage } from "@/features/auth/LoginPage";
import { DocumentsPage } from "@/features/documents/DocumentsPage";
import { TemplateEditorPage } from "@/features/templates/TemplateEditorPage";
import { TemplatesPage } from "@/features/templates/TemplatesPage";
import { WorkspaceDetailPage } from "@/features/workspaces/WorkspaceDetailPage";
import { WorkspacesPage } from "@/features/workspaces/WorkspacesPage";
import { RequireAuth } from "@/routes/RequireAuth";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/workspaces" element={<WorkspacesPage />} />
          <Route path="/workspaces/:workspaceId" element={<WorkspaceDetailPage />} />
          <Route path="/workspaces/:workspaceId/templates" element={<TemplatesPage />} />
          <Route
            path="/workspaces/:workspaceId/templates/:templateId"
            element={<TemplateEditorPage />}
          />
          <Route path="/workspaces/:workspaceId/documents" element={<DocumentsPage />} />
          <Route path="/" element={<Navigate to="/workspaces" replace />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
