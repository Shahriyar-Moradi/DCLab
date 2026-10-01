"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { z } from "zod";
import {
  setActiveWorkspaceId,
  clearWorkspaceQueries,
  prepareWorkspaceTransition,
} from "@/lib/infrastructure/active-workspace";
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
  capability_matrix_version: z.string(),
  capabilities: z.record(z.string(), z.boolean()),
  request_id: z.string().nullable().optional(),
});

type SessionContextValue = {
  user: SessionUser | null;
  loaded: boolean;
  activeWorkspaceId: string | null;
  workspaces: SessionWorkspace[];
  activeWorkspace: SessionWorkspace | null;
  workspaceSwitching: boolean;
  selectWorkspace: (workspaceId: string) => Promise<SessionUser>;
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
    return parseSessionUser(data);
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [workspaceSwitching, setWorkspaceSwitching] = useState(false);
  const sessionSyncRevision = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const sync = () => {
      const revision = ++sessionSyncRevision.current;
      void loadMe().then((next) => {
        if (!cancelled && revision === sessionSyncRevision.current) {
          setUser(applyUser(next));
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
    sessionSyncRevision.current += 1;
    setUser(applyUser(next));
    setLoaded(true);
    notifySessionChanged();
  }, []);

  const signOut = useCallback(async () => {
    sessionSyncRevision.current += 1;
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
      const previous = user;
      setWorkspaceSwitching(true);
      setActiveWorkspaceId(null);
      await prepareWorkspaceTransition(queryClient);
      try {
        const data = await apiPut("/auth/workspace", MeSchema, { workspace_id: workspaceId });
        const next = parseSessionUser(data);
        if (!next || next.active_workspace_id !== workspaceId) {
          throw new Error("The server did not confirm the selected workspace.");
        }
        applyUser(next);
        clearWorkspaceQueries(queryClient);
        setUser(next);
        return next;
      } catch (error) {
        applyUser(previous);
        clearWorkspaceQueries(queryClient);
        throw error;
      } finally {
        setWorkspaceSwitching(false);
      }
    },
    [queryClient, user],
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
      workspaceSwitching,
      selectWorkspace,
      signIn,
      signOut,
    }),
    [
      user,
      loaded,
      activeWorkspaceId,
      workspaces,
      activeWorkspace,
      workspaceSwitching,
      selectWorkspace,
      signIn,
      signOut,
    ],
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
