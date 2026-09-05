"use client";

import { cn } from "@/lib/cn";
import { X } from "lucide-react";
import { useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconButton } from "./IconButton";
import { useBodyScrollLock, useEscape, useFocusTrap } from "./overlay";

export function Drawer({
  open,
  onClose,
  title,
  children,
  side = "right",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  side?: "left" | "right";
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useBodyScrollLock(open);
  useEscape(open, onClose);
  useFocusTrap(open, panelRef);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="app-overlay fixed inset-0 z-[70]">
      <button type="button" className="absolute inset-0 bg-midnight/40" aria-label="Close drawer" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={cn(
          "app-drawer-enter absolute top-0 flex h-full w-full max-w-md flex-col border-hairline bg-paper-raised shadow-lg sm:w-sidebar",
          side === "left" ? "left-0 border-r" : "right-0 border-l",
        )}
      >
        <div className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-3">
          <h2 id={titleId} className="min-w-0 break-words font-sans text-section text-ink">
            {title}
          </h2>
          <IconButton label="Close" onClick={onClose}>
            <X size={18} aria-hidden />
          </IconButton>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 text-body text-ink">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
