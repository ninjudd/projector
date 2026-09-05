import type { ReactNode } from "react";

export function Section({
  id,
  title,
  lead,
  children,
  size = "default",
}: {
  id: string;
  title: string;
  lead?: ReactNode;
  children: ReactNode;
  size?: "default" | "tight";
}) {
  const padding = size === "tight" ? "py-14 sm:py-16" : "py-20 sm:py-24";
  return (
    <section id={id} className="border-t border-line">
      <div className={`mx-auto max-w-6xl px-6 ${padding}`}>
        <div className="max-w-2xl">
          <h2 className="text-3xl font-semibold tracking-tight text-fg">{title}</h2>
          {lead ? <p className="mt-4 text-lg leading-relaxed text-muted">{lead}</p> : null}
        </div>
        <div className="mt-10">{children}</div>
      </div>
    </section>
  );
}
