"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "dclab.sidebarCollapsed";

export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(sessionStorage.getItem(STORAGE_KEY) === "1");
  }, []);

  const toggle = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      sessionStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  }, []);

  return { collapsed, toggle };
}
