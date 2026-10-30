import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAdmin, RequireAuth } from "./components/guards";
import { Layout } from "./components/Layout";
import { AdminSamplesPage } from "./pages/admin/AdminSamplesPage";
import { AuditPage } from "./pages/admin/AuditPage";
import { UsersPage } from "./pages/admin/UsersPage";
import { LoginPage } from "./pages/LoginPage";
import { SampleDetailRoute } from "./pages/SampleDetailPage";
import { SamplesPage } from "./pages/SamplesPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/samples" element={<SamplesPage />} />
        <Route path="/samples/:id" element={<SampleDetailRoute />} />
        <Route path="/admin/users" element={<RequireAdmin><UsersPage /></RequireAdmin>} />
        <Route path="/admin/samples" element={<RequireAdmin><AdminSamplesPage /></RequireAdmin>} />
        <Route path="/admin/audit" element={<RequireAdmin><AuditPage /></RequireAdmin>} />
      </Route>
      <Route path="*" element={<Navigate to="/samples" replace />} />
    </Routes>
  );
}
