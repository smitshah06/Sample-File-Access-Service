import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, SESSION_EXPIRED_EVENT, type Me } from "./api";

interface AuthState {
  me: Me | null;
  loading: boolean;
  /** True after an explicit sign-out, so the next user isn't sent to the last user's page. */
  signedOut?: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [signedOut, setSignedOut] = useState(false);

  useEffect(() => {
    // The session cookie is HttpOnly, so the only way to know who we are is to ask.
    api
      .me()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setLoading(false));

    const expired = () => setMe(null);
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setMe(await api.login(username, password));
    setSignedOut(false);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setSignedOut(true);
      setMe(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ me, loading, signedOut, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

export { AuthContext };
