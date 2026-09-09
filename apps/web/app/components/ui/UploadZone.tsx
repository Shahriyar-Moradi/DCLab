"use client";

import { cn } from "@/lib/cn";
import { Upload } from "lucide-react";
import { useId, useState, type ChangeEvent, type DragEvent } from "react";

export function UploadZone({
  accept,
  multiple = false,
  disabled = false,
  label = "Drop a file here, or click to choose",
  hint,
  error,
  onFiles,
  className,
}: {
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  label?: string;
  hint?: string;
  error?: string;
  onFiles: (files: File[]) => void;
  className?: string;
}) {
  const [drag, setDrag] = useState(false);
  const inputId = useId();

  function take(files: FileList | null) {
    if (!files || disabled) return;
    const list = Array.from(files);
    if (list.length) onFiles(list);
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDrag(false);
    take(event.dataTransfer.files);
  }

  function onChange(event: ChangeEvent<HTMLInputElement>) {
    take(event.target.files);
    event.target.value = "";
  }

  return (
    <div className={className}>
      <label
        htmlFor={inputId}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-hairline bg-paper-raised px-4 py-8 text-center transition-ui sm:px-8 sm:py-16",
          drag && "border-navy bg-navy-soft",
          disabled && "cursor-not-allowed opacity-50",
          error && "border-oxblood",
        )}
      >
        <input
          id={inputId}
          type="file"
          accept={accept}
          multiple={multiple}
          disabled={disabled}
          className="sr-only"
          onChange={onChange}
        />
        <Upload className="mb-3 h-5 w-5 text-ink-muted" aria-hidden />
        <span className="max-w-full break-all font-sans text-body text-ink">{label}</span>
        {hint ? <span className="mt-2 text-helper text-ink-muted">{hint}</span> : null}
      </label>
      {error ? (
        <p className="mt-2 text-helper text-oxblood" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
