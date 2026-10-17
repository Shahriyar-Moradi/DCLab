"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";
import { useSession } from "@/lib/application";

/** Sign out (server session revoked, local state cleared), then go to the sign-in page. Shared by every app frame. */
export function useSignOutToLogin(): () => void {
  const router = useRouter();
  const { signOut } = useSession();
  return useCallback(() => {
    void signOut().then(() => {
      router.push("/login");
      router.refresh();
    });
  }, [router, signOut]);
}
