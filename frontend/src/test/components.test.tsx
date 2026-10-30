import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { ApiError, type FileInfo } from "../api";
import { FileRow } from "../components/FileRow";
import { RequireAdmin, RequireAuth } from "../components/guards";
import { LoginPage } from "../pages/LoginPage";
import { alice, renderAs, root } from "./render";

const file = (qc_status: FileInfo["qc_status"]): FileInfo => ({
  id: "f1",
  filename: "tumor.bam",
  qc_status,
  qc_updated_at: null,
  created_at: "2026-10-29T14:00:00Z",
  has_content: true,
  size_bytes: 2048,
  sha256: "abc",
  content_type: "application/octet-stream",
});

const row = (ui: React.ReactElement) => (
  <table>
    <tbody>{ui}</tbody>
  </table>
);

describe("FileRow download button", () => {
  it.each(["pending", "failed"] as const)("is disabled while QC is %s", (status) => {
    renderAs(alice, row(<FileRow file={file(status)} access="owner" />));
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
  });

  it("is enabled for a passed file the user can access", () => {
    renderAs(alice, row(<FileRow file={file("passed")} access="grantee" />));
    expect(screen.getByRole("button", { name: "Download" })).toBeEnabled();
  });

  it("is disabled for an admin with no grant, even when QC passed", () => {
    renderAs(root, row(<FileRow file={file("passed")} access="none" />));
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
  });

  it("shows the file ID (needed to report QC results)", () => {
    renderAs(alice, row(<FileRow file={file("pending")} access="owner" />));
    expect(screen.getByTitle("File ID")).toHaveTextContent("f1");
  });

  it("only shows Delete when the owner can delete", () => {
    renderAs(alice, row(<FileRow file={file("passed")} access="grantee" />));
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });
});

describe("route guards", () => {
  const routes = (
    <Routes>
      <Route path="/login" element={<p>login page</p>} />
      <Route
        path="/admin"
        element={
          <RequireAuth>
            <RequireAdmin>
              <p>admin area</p>
            </RequireAdmin>
          </RequireAuth>
        }
      />
    </Routes>
  );

  it("sends anonymous users to sign-in", () => {
    renderAs(null, routes, "/admin");
    expect(screen.getByText("login page")).toBeInTheDocument();
  });

  it("hides admin pages from users", () => {
    renderAs(alice, routes, "/admin");
    expect(screen.queryByText("admin area")).not.toBeInTheDocument();
    expect(screen.getByText(/admin role/)).toBeInTheDocument();
  });

  it("shows admin pages to admins", () => {
    renderAs(root, routes, "/admin");
    expect(screen.getByText("admin area")).toBeInTheDocument();
  });
});

describe("LoginPage", () => {
  it("submits credentials and shows the server's error", async () => {
    const { auth } = renderAs(null, <LoginPage />, "/login");
    auth.login.mockRejectedValue(
      new ApiError(401, "invalid_credentials", "invalid username or password"),
    );

    await userEvent.type(screen.getByLabelText("Username"), "alice");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(auth.login).toHaveBeenCalledWith("alice", "wrong-password");
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid username or password");
  });
});
