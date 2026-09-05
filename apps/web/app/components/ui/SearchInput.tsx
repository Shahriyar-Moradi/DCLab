"use client";

import { cn } from "@/lib/cn";
import { Search, X } from "lucide-react";
import type { InputHTMLAttributes } from "react";
import { controlClass, controlErrorClass, controlHeightClass } from "./control";
import { IconButton } from "./IconButton";

type SearchInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "onChange"> & {
  value: string;
  onChange: (value: string) => void;
  error?: string;
  onSubmitSearch?: () => void;
};

export function SearchInput({
  value,
  onChange,
  error,
  onSubmitSearch,
  className,
  id,
  placeholder = "Search",
  disabled,
  ...props
}: SearchInputProps) {
  return (
    <div className={cn("relative min-w-0", className)}>
      <Search
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-muted"
        aria-hidden
      />
      <input
        id={id}
        type="search"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") onSubmitSearch?.();
        }}
        className={cn(controlClass, controlHeightClass, "pl-9 pr-9", error && controlErrorClass)}
        {...props}
        aria-label={props["aria-label"] ?? placeholder}
      />
      {value && !disabled ? (
        <span className="absolute right-1 top-1/2 -translate-y-1/2">
          <IconButton label="Clear search" onClick={() => onChange("")}>
            <X size={16} aria-hidden />
          </IconButton>
        </span>
      ) : null}
      {error ? (
        <p className="mt-1.5 text-helper text-oxblood" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
