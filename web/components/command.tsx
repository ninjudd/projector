import { CopyButton } from "./copy-button";

export function Command({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div
      className={`flex items-center gap-3 rounded-lg border border-line bg-surface py-2 pl-4 pr-2 font-mono text-[13px] ${className}`}
    >
      <span className="select-none text-accent" aria-hidden="true">
        $
      </span>
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap py-1 text-fg">{text}</code>
      <CopyButton text={text} />
    </div>
  );
}

export function CommandList({ commands }: { commands: string[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface font-mono text-[13px]">
      {commands.map((command) => (
        <div
          key={command}
          className="flex items-center gap-3 border-b border-line py-2 pl-4 pr-2 last:border-b-0"
        >
          <span className="select-none text-accent" aria-hidden="true">
            $
          </span>
          <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap py-1 text-fg">
            {command}
          </code>
          <CopyButton text={command} />
        </div>
      ))}
    </div>
  );
}
