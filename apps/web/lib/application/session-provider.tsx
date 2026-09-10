"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { z } from "zod";
import { setActiveWorkspaceId, clearWorkspaceQueries } from "@/lib/infrastructure/active-workspace";
import { apiGet, apiPostEmpty, apiPut } from "@/lib/infrastructure/api-client";
import {
  SESSION_CHANGED_EVENT,
  notifySessionChanged,
  parseSessionUser,
  type SessionUser,
  type SessionWorkspace,
} from "@/lib/infrastructure/session";

const WorkspaceOptionSchema = z.object({
  id: z.string(),
  slug: z.string(),
  name: z.string(),
  kind: z.string(),
  role: z.string().nullable().optional(),
});

const MeSchema = z.object({
  id: z.string(),
  email: z.string(),
  role: z.enum([
    "dclab_admin",
    "dclab_developer",
    "business_admin",
    "business_developer",
    "personal_developer",
    "client_user",
    "workspace_owner",
    "workspace_admin",
    "ml_engineer",
    "viewer",
  ]),
  full_name: z.string(),
  workspace_id: z.string().nullable(),
  email_verified_at: z.string().nullable().optional(),
  active_workspace_id: z.string().nullable().optional(),
  workspaces: z.array(WorkspaceOptionSchema).optional(),
  request_id: z.string().nullable().optional(),
});

type SessionContextValue = {
  user: SessionUser | null;
  loaded: boolean;
  activeWorkspaceId: string | null;
  workspaces: SessionWorkspace[];
  activeWorkspace: SessionWorkspace | null;
  selectWorkspace: (workspaceId: string) => Promise<void>;
  signIn: (user: SessionUser) => void;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

function applyUser(user: SessionUser | null): SessionUser | null {
  setActiveWorkspaceId(user?.active_workspace_id ?? null);
  return user;
}

async function loadMe(): Promise<SessionUser | null> {
  try {
    const data = await apiGet("/auth/me", MeSchema);
    return applyUser(parseSessionUser(data));
  } catch {
    applyUser(null);
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const sync = () => {
      void loadMe().then((next) => {
        if (!cancelled) {
          setUser(next);
          setLoaded(true);
        }
      });
    };
    sync();
    window.addEventListener(SESSION_CHANGED_EVENT, sync);
    return () => {
      cancelled = true;
      window.removeEventListener(SESSION_CHANGED_EVENT, sync);
    };
  }, []);

  const signIn = useCallback((next: SessionUser) => {
    setUser(applyUser(next));
    notifySessionChanged();
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiPostEmpty("/auth/logout");
    } catch {
      /* local sign-out still proceeds */
    }
    applyUser(null);
    clearWorkspaceQueries(queryClient);
    setUser(null);
    notifySessionChanged();
  }, [queryClient]);

  const selectWorkspace = useCallback(
    async (workspaceId: string) => {
      const data = await apiPut("/auth/workspace", MeSchema, { workspace_id: workspaceId });
      const next = parseSessionUser(data);
      applyUser(next);
      clearWorkspaceQueries(queryClient);
      setUser(next);
    },
    [queryClient],
  );

  const workspaces = useMemo(() => user?.workspaces ?? [], [user]);
  const activeWorkspaceId = user?.active_workspace_id ?? null;
  const activeWorkspace = useMemo(
    () => workspaces.find((row) => row.id === activeWorkspaceId) ?? null,
    [workspaces, activeWorkspaceId],
  );

  const value = useMemo(
    () => ({
      user,
      loaded,
      activeWorkspaceId,
      workspaces,
      activeWorkspace,
      selectWorkspace,
      signIn,
      signOut,
    }),
    [user, loaded, activeWorkspaceId, workspaces, activeWorkspace, selectWorkspace, signIn, signOut],
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
