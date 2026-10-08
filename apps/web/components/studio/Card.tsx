import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type CardProps = {
  title?: ReactNode;
  /** Right-aligned meta next to the title. */
  aside?: ReactNode;
  flat?: boolean;
  as?: "section" | "div" | "article";
  className?: string;
  children?: ReactNode;
};

export function Card({ title, aside, flat, as: Tag = "section", className, children }: CardProps) {
  return (
    <Tag className={cn("card", flat && "flat", className)}>
      {title ? (
        <h2>
          {title}
          {aside ? <span className="sp">{aside}</span> : null}
        </h2>
      ) : null}
      {children}
    </Tag>
  );
}
