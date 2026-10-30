import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth";

// These guards only shape the UI. Every rule is enforced by the backend, which is the
// single source of truth; hiding a page here is a convenience, not a security control.

export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading, signedOut } = useAuth();
  const location = useLocation();
  if (loading) return <p className="muted">Loading…</p>;
  if (!me) {
    // Remember where to return to after an expired session, but not after an explicit
    // sign-out: the next person to sign in shouldn't land on the previous user's page.
    const state = signedOut ? undefined : { from: location.pathname };
    return <Navigate to="/login" replace state={state} />;
  }
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  if (me?.role !== "admin") {
    return <p className="error">You need the admin role to see this page.</p>;
  }
  return <>{children}</>;
}
