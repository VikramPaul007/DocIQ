import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { ApiError, api, setCsrfToken } from "@/lib/api";
import type { Me } from "@/lib/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.get<Me>("/auth/me");
      setCsrfToken(data.csrf_token);
      setMe(data);
    } catch (err) {
      setCsrfToken(null);
      setMe(null);
      if (!(err instanceof ApiError && err.status === 401)) {
        console.error(err);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const data = await api.post<Me>("/auth/login", { email, password });
    setCsrfToken(data.csrf_token);
    setMe(data);
  }, []);

  const logout = useCallback(async () => {
    await api.post("/auth/logout");
    setCsrfToken(null);
    setMe(null);
  }, []);

  return <AuthContext.Provider value={{ me, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
