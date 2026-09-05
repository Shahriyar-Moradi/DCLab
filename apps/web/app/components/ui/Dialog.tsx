"use client";

import { X } from "lucide-react";
import { useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconButton } from "./IconButton";
import { useBodyScrollLock, useEscape, useFocusTrap } from "./overlay";

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useBodyScrollLock(open);
  useEscape(open, onClose);
  useFocusTrap(open, panelRef);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="app-overlay fixed inset-0 z-[70] flex items-end justify-center p-4 sm:items-center">
      <button type="button" className="absolute inset-0 bg-midnight/40" aria-label="Close dialog" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative flex max-h-[min(90dvh,40rem)] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl border border-hairline bg-paper-raised p-5 shadow-lg sm:rounded-2xl"
      >
        <div className="flex shrink-0 items-start justify-between gap-3">
          <h2 id={titleId} className="min-w-0 break-words font-sans text-section text-ink">
            {title}
          </h2>
          <IconButton label="Close" onClick={onClose}>
            <X size={18} aria-hidden />
          </IconButton>
        </div>
        <div className="mt-4 min-h-0 flex-1 overflow-y-auto text-body text-ink">{children}</div>
        {footer ? <div className="mt-6 flex shrink-0 flex-wrap justify-end gap-2">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
