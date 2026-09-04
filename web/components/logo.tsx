import Link from "next/link";

export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={className}>
      <path d="M7 16 L29 5.5 V26.5 Z" fill="var(--accent)" />
      <circle cx="7" cy="16" r="4.5" fill="currentColor" />
    </svg>
  );
}

export function Logo() {
  return (
    <Link
      href="/"
      className="flex items-center gap-2.5 text-[15px] font-semibold tracking-tight text-fg"
    >
      <Mark className="h-5 w-5" />
      Projector
    </Link>
  );
}
