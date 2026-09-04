import { codeToHtml, type BundledLanguage } from "shiki";

export async function CodeBlock({
  code,
  lang,
  title,
  className = "",
}: {
  code: string;
  lang: BundledLanguage;
  title?: string;
  className?: string;
}) {
  const html = await codeToHtml(code.trim(), {
    lang,
    themes: { light: "vitesse-light", dark: "vitesse-dark" },
    defaultColor: false,
  });

  return (
    <figure className={`overflow-hidden rounded-xl border border-line bg-surface ${className}`}>
      {title ? (
        <figcaption className="border-b border-line px-4 py-2 font-mono text-xs text-faint">
          {title}
        </figcaption>
      ) : null}
      <div
        className="overflow-x-auto p-4 font-mono text-[13px] leading-6 [&_pre]:m-0"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </figure>
  );
}
