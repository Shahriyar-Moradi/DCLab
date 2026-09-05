"use client";

import { AppSidebar } from "@/app/components/layout/AppSidebar";
import { useBodyScrollLock, useEscape, useFocusTrap } from "@/app/components/ui/overlay";
import { X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

type AppMobileDrawerProps = {
  open: boolean;
  onClose: () => void;
};

export function AppMobileDrawer({ open, onClose }: AppMobileDrawerProps) {
  const pathname = usePathname();
  const previousPathname = useRef(pathname);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && pathname !== previousPathname.current) onClose();
    previousPathname.current = pathname;
  }, [onClose, open, pathname]);

  useBodyScrollLock(open);
  useEscape(open, onClose);
  useFocusTrap(open, panelRef);

  if (!open) return null;

  return (
    <div className="app-mobile-drawer lg:hidden">
      <button
        type="button"
        className="app-mobile-drawer-backdrop"
        aria-label="Close navigation"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        id="app-mobile-navigation"
        className="app-mobile-drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Application navigation"
      >
        <button type="button" className="app-mobile-drawer-close" aria-label="Close navigation" onClick={onClose}>
          <X size={20} aria-hidden />
        </button>
        <AppSidebar mobile onNavigate={onClose} />
      </div>
    </div>
  );
}
