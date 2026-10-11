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
  ABILITY_SCOPES,
  TOKEN_LIMIT_TEXT,
  abilityHelp,
  abilityLabel,
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

/** Use DCLab from code: Python, terminal, MCP and the REST API. Static text; tokens are placeholders only. */
export function AgentsConnect() {
  return (
    <>
      <SectionTabs
        title="Use DCLab from code"
        idPrefix="connect"
        sections={[
          {
            id: "mcp",
            label: "Claude Code & Cursor",
            content: (
              <>
                <p>Save this as <code>.mcp.json</code> in your project. Replace <code>dclab_st_…</code> with an access token from the Access tokens tab, and do not commit the file with a real token in it.</p>
                <Snippet code={MCP_JSON} label=".mcp.json example" language="json" />
                <Snippet code={MCP_INSTALL} label="MCP install commands" language="shell" />
                <p className="small muted">DCLAB_API_URL is the DCLab API service itself (for example http://localhost:8001 locally), not the address you open in the browser. Tools that read are on by default. Tools that change things are listed only when you also set DCLAB_MCP_WRITE_ENABLED=1. A token cannot accept decisions; a person approves them in the Inbox. MCP tools return bounded results, but the token itself can read anything its permissions allow, including prediction files. Final test set results are withheld from access tokens.</p>
              </>
            ),
          },
          {
            id: "sdk",
            label: "Python",
            content: (
              <>
                <p>The Python library talks to DCLab&apos;s <code>/v1</code> web API. Set <code>DCLAB_API_URL</code> and <code>DCLAB_TOKEN</code> in your environment first.</p>
                <Snippet code={SDK_PYTHON} label="Python SDK example" language="python" />
              </>
            ),
          },
          {
            id: "cli",
            label: "Terminal",
            content: (
              <>
                <p>The command is <code>dclab-cli</code>. Add <code>--json</code> to any command for machine-readable output. Commands that create things take <code>--idempotency-key</code>, so a retry is safe.</p>
                <Snippet code={CLI_SHELL} label="CLI example" language="shell" />
              </>
            ),
          },
          {
            id: "api",
            label: "Web API",
            content: (
              <>
                <KeyValue items={V1_CONVENTIONS} />
                <p><a href={OPENAPI_HREF} target="_blank" rel="noopener noreferrer">OpenAPI document (JSON)</a></p>
              </>
            ),
          },
        ]}
      />
      <Card title="What a connected tool can do">
        <p>{WRITE_SWITCH_TEXT}</p>
        <KeyValue items={ABILITY_SCOPES.map((scope) => ({ key: scope, label: abilityLabel(scope), value: abilityHelp(scope) ?? "" }))} />
        <p><b>{TOKEN_LIMIT_TEXT}</b></p>
      </Card>
    </>
  );
}
