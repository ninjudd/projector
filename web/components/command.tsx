import { Fragment } from "react";
import { CopyButton } from "./copy-button";

function CommandRow({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`flex items-start gap-3 py-2 pl-4 pr-2 leading-6 ${className}`}>
      <span className="select-none py-1 text-accent" aria-hidden="true">
        $
      </span>
      <code className="min-w-0 flex-1 py-1 text-fg">
        {text.split(" ").map((word, index) => (
          <Fragment key={index}>
            {index > 0 ? " " : null}
            <span className="inline-block max-w-full [overflow-wrap:anywhere]">{word}</span>
          </Fragment>
        ))}
      </code>
      <CopyButton text={text} />
    </div>
  );
}

export function Command({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`rounded-lg border border-line bg-surface font-mono text-[12px] sm:text-[13px] ${className}`}>
      <CommandRow text={text} />
    </div>
  );
}

export function CommandList({ commands }: { commands: string[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface font-mono text-[12px] sm:text-[13px]">
      {commands.map((command) => (
        <CommandRow key={command} text={command} className="border-b border-line last:border-b-0" />
      ))}
    </div>
  );
}
