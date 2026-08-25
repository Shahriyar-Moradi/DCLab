import { cn } from "@/lib/cn";
import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost";

const variants: Record<Variant, string> = {
  primary: "bg-brand-gradient shadow-brand text-white hover:opacity-95 disabled:opacity-50",
  secondary: "border border-hairline bg-paper-raised text-ink hover:bg-navy-soft disabled:opacity-50",
  ghost: "text-ink hover:bg-navy-soft disabled:opacity-50",
};

export function Button({
  variant = "primary",
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center rounded-full px-5 py-2.5 font-body text-body font-semibold transition-colors",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
