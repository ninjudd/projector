import { DOCS, GITHUB } from "@/lib/links";
import { GitHubIcon } from "./icons";
import { Logo } from "./logo";

const nav = [
  { href: "#how-it-works", label: "How it works" },
  { href: "#cli", label: "CLI" },
  { href: "#agents", label: "Agents" },
  { href: "#review", label: "Review loops" },
];

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg/80 backdrop-blur-md">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-fg focus:px-3 focus:py-2 focus:text-bg"
      >
        Skip to content
      </a>
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Logo />
        <nav aria-label="Primary" className="hidden items-center gap-8 text-sm text-muted md:flex">
          {nav.map((item) => (
            <a key={item.href} href={item.href} className="transition-colors hover:text-fg">
              {item.label}
            </a>
          ))}
          <a
            href={DOCS.index}
            className="transition-colors hover:text-fg"
            target="_blank"
            rel="noreferrer"
          >
            Docs
          </a>
        </nav>
        <div className="flex items-center gap-2">
          <a
            href={GITHUB}
            aria-label="Projector on GitHub"
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-9 w-9 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <GitHubIcon className="h-5 w-5" />
          </a>
          <a
            href="#install"
            className="rounded-full bg-fg px-4 py-2 text-sm font-medium text-bg transition-colors hover:bg-accent hover:text-accent-fg"
          >
            Install
          </a>
        </div>
      </div>
    </header>
  );
}
