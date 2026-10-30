import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import type { Me } from "../api";
import { AuthContext } from "../auth";

/** Render with a fixed signed-in user (or none) and an in-memory router. */
export function renderAs(me: Me | null, ui: ReactElement, path = "/") {
  const auth = { me, loading: false, login: vi.fn(), logout: vi.fn() };
  return {
    auth,
    ...render(
      <AuthContext.Provider value={auth}>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </AuthContext.Provider>,
    ),
  };
}

export const alice: Me = { id: "u-alice", username: "alice", role: "user" };
export const root: Me = { id: "u-root", username: "root", role: "admin" };
