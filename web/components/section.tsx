import type { ReactNode } from "react";

export function Section({
  id,
  eyebrow,
  title,
  lead,
  children,
  className = "",
}: {
  id: string;
  eyebrow: string;
  title: ReactNode;
  lead?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`border-t border-line ${className}`}>
      <div className="mx-auto max-w-6xl px-6 py-24 sm:py-28">
        <div className="max-w-2xl">
          <p className="eyebrow">{eyebrow}</p>
          <h2 className="mt-4 font-display text-4xl leading-[1.05] tracking-tight text-fg sm:text-5xl">
            {title}
          </h2>
          {lead ? <p className="mt-5 text-lg leading-relaxed text-muted">{lead}</p> : null}
        </div>
        <div className="mt-14">{children}</div>
      </div>
    </section>
  );
}
