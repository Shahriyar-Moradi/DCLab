"use client";

import { Card } from "@/components/studio/Card";
import { CodeBlock } from "@/components/studio/CodeBlock";
import { KeyValue } from "@/components/studio/KeyValue";
import { SectionTabs } from "@/components/studio/SectionTabs";
import {
  CLI_SHELL,
  MCP_INSTALL,
  MCP_JSON,
  OPENAPI_HREF,
  SCOPE_HELP,
  SDK_PYTHON,
  V1_CONVENTIONS,
  WRITE_SWITCH_TEXT,
} from "@/lib/application/studio-agents";
import { CopyButton } from "./CopyButton";

function Snippet({ code, label, language }: { code: string; label: string; language: string }) {
  return (
    <>
      <CodeBlock code={code} label={label} language={language} />
      <CopyButton text={code} label={`Copy ${label}`} />
    </>
  );
}

/** Connect tab: MCP, SDK, CLI and /v1 conventions. Static text; tokens are placeholders only. */
export function AgentsConnect() {
  return (
    <>
      <SectionTabs
        title="Connect"
        idPrefix="connect"
        sections={[
          {
            id: "mcp",
            label: "MCP (Claude Code, Cursor)",
            content: (
              <>
                <p>Save this as <code>.mcp.json</code> in your project. Replace <code>dclab_st_…</code> with a token from the Service tokens tab, and do not commit the file with a real token in it.</p>
                <Snippet code={MCP_JSON} label=".mcp.json example" language="json" />
                <Snippet code={MCP_INSTALL} label="MCP install commands" language="shell" />
                <p className="small muted">DCLAB_API_URL is the DCLab API service itself (for example http://localhost:8001 locally), not the address you open in the browser. Read tools are on by default; outputs are bounded and never contain raw rows or final holdout values.</p>
              </>
            ),
          },
          {
            id: "sdk",
            label: "Python SDK",
            content: (
              <>
                <p>The SDK talks only to <code>/v1</code>. Set <code>DCLAB_API_URL</code> and <code>DCLAB_TOKEN</code> in your environment first.</p>
                <Snippet code={SDK_PYTHON} label="Python SDK example" language="python" />
              </>
            ),
          },
          {
            id: "cli",
            label: "CLI",
            content: (
              <>
                <p>The command is <code>dclab-cli</code>. Add <code>--json</code> to any command for machine output. Commands that create things take <code>--idempotency-key</code>.</p>
                <Snippet code={CLI_SHELL} label="CLI example" language="shell" />
              </>
            ),
          },
          {
            id: "api",
            label: "REST API (/v1)",
            content: (
              <>
                <KeyValue items={V1_CONVENTIONS} />
                <p><a href={OPENAPI_HREF} target="_blank" rel="noopener noreferrer">OpenAPI document (JSON)</a></p>
              </>
            ),
          },
        ]}
      />
      <Card title="Read-only or read and write">
        <p>{WRITE_SWITCH_TEXT}</p>
        <KeyValue items={SCOPE_HELP.map((item) => ({ key: item.scope, label: <code>{item.scope}</code>, value: item.help }))} />
      </Card>
    </>
  );
}
