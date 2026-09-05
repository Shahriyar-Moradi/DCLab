export function CapabilityNotice({ name }: { name: string }) {
  return (
    <section className="rounded-xl border border-hairline bg-paper-raised p-5">
      <p className="font-mono text-data text-ink-muted">{name} is not enabled for this workspace.</p>
    </section>
  );
}
