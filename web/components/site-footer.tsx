import type { ReactNode } from "react";
import { DOCS, GITHUB } from "@/lib/links";
import { cliVersion, pluginVersion } from "@/lib/versions";
import { Logo } from "./logo";

function FooterLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-sm text-muted transition-colors hover:text-fg"
    >
      {children}
    </a>
  );
}

export function SiteFooter() {
  const cli = cliVersion();
  const plugin = pluginVersion();
  const versions = [cli && `CLI ${cli}`, plugin && `plugin ${plugin}`].filter(Boolean);

  return (
    <footer className="border-t border-line">
      <div className="mx-auto grid max-w-6xl gap-12 px-6 py-14 md:grid-cols-[1.6fr_1fr_1fr]">
        <div>
          <Logo />
          <p className="mt-4 max-w-sm text-sm leading-relaxed text-muted">
            Git-native project plans for people and coding agents. Open source under the MIT
            license.
          </p>
          {versions.length > 0 ? (
            <p className="mt-4 font-mono text-xs text-faint">{versions.join(" · ")}</p>
          ) : null}
        </div>
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium text-fg">Documentation</h3>
          <FooterLink href={DOCS.convention}>Project convention</FooterLink>
          <FooterLink href={DOCS.cli}>CLI reference</FooterLink>
          <FooterLink href={DOCS.plugins}>Plugin guide</FooterLink>
          <FooterLink href={DOCS.reviewLoop}>Review loop</FooterLink>
          <FooterLink href={DOCS.fixLoop}>Fix loop</FooterLink>
        </div>
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium text-fg">Project</h3>
          <FooterLink href={GITHUB}>GitHub</FooterLink>
          <FooterLink href={DOCS.issues}>Issues</FooterLink>
          <FooterLink href={DOCS.license}>MIT license</FooterLink>
          <FooterLink href="https://github.com/ninjudd">Built by Justin Balthrop</FooterLink>
        </div>
      </div>
    </footer>
  );
}
