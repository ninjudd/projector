import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";

export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main
        id="main"
        className="mx-auto flex w-full max-w-6xl flex-1 flex-col justify-center px-6 py-32"
      >
        <p className="eyebrow">404</p>
        <h1 className="mt-4 font-display text-5xl tracking-tight sm:text-6xl">
          No project at <em className="text-accent">this path.</em>
        </h1>
        <p className="mt-5 max-w-xl text-lg text-muted">
          Projector names a project by its path. This one does not resolve.
        </p>
        <Link
          href="/"
          className="mt-10 inline-flex w-fit items-center gap-2 rounded-full bg-fg px-5 py-2.5 text-sm font-medium text-bg transition-colors hover:bg-accent hover:text-accent-fg"
        >
          Back to the front page
        </Link>
      </main>
      <SiteFooter />
    </>
  );
}
