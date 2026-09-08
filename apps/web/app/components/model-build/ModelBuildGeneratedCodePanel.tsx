"use client";

import { Badge } from "@/app/components/ui/Badge";
import { Button } from "@/app/components/ui/Button";
import type { ModelBuildStage } from "@/lib/domain";
import { useState } from "react";

export function ModelBuildGeneratedCodePanel({ stage }: { stage: ModelBuildStage }) {
  const code = stage.generated_code;
  const [copied, setCopied] = useState(false);
  if (!code) return null;

  const tone =
    code.code_generation_support_status === "supported"
      ? "green"
      : code.code_generation_support_status === "not_available"
        ? "amber"
        : "neutral";

  async function copySource() {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code.source);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="model-build-code">
      <div className="model-build-code-header">
        <div className="min-w-0">
          <p className="product-eyebrow">Generated Python</p>
          <p className="mt-1 font-mono text-data text-ink-muted">
            {code.generator_version} · {code.digest.slice(0, 12)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={tone} emphasis="soft">
            {code.code_generation_support_status.replaceAll("_", " ")}
          </Badge>
          <Button variant="secondary" size="sm" onClick={() => void copySource()}>
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>
      {code.helper_requirements.length > 0 ? (
        <ul className="model-build-code-helpers">
          {code.helper_requirements.map((helper) => (
            <li key={helper}>{helper}</li>
          ))}
        </ul>
      ) : null}
      <pre className="model-build-code-source">
        <code>{code.source}</code>
      </pre>
    </div>
  );
}
