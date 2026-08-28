"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  SESSION_CHANGED_EVENT,
  clearToken,
  readSessionUser,
  storeToken,
  type SessionUser,
} from "@/lib/infrastructure/session";

type SessionContextValue = {
  user: SessionUser | null;
  loaded: boolean;
  signIn: (token: string, user?: SessionUser) => void;
  signOut: () => void;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setUser(readSessionUser());
    setLoaded(true);
    const sync = () => setUser(readSessionUser());
    window.addEventListener(SESSION_CHANGED_EVENT, sync);
    return () => window.removeEventListener(SESSION_CHANGED_EVENT, sync);
  }, []);

  const signIn = useCallback((token: string, next?: SessionUser) => {
    storeToken(token);
    setUser(next ?? readSessionUser());
  }, []);

  const signOut = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loaded, signIn, signOut }),
    [user, loaded, signIn, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error("useSession must be used inside SessionProvider");
  }
  return context;
}
