import Image, { type StaticImageData } from "next/image";
import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import overviewDark from "@/public/summaries/overview-dark.png";
import overviewLight from "@/public/summaries/overview-light.png";
import sectionDark from "@/public/summaries/section-dark.png";
import sectionLight from "@/public/summaries/section-light.png";
import { DOCS, SUMMARY_EXAMPLE } from "@/lib/links";

const parts: { title: string; body: ReactNode }[] = [
  {
    title: "What the pull request does, for someone new to the code",
    body: (
      <>
        The overview says what the part of the system is for, what problem the change solves, and
        how, and defines each term a newcomer would trip on.
      </>
    ),
  },
  {
    title: "Where the real review is",
    body: (
      <>
        The page counts hand-written, test, generated, and documentation lines apart, and folds
        generated code, tests, and docs away so the files you must read come first.
      </>
    ),
  },
  {
    title: "Sections in reading order",
    body: (
      <>
        The diff is split into logical sections, from the contract outward: interfaces first, then
        the core, then the callers. Each section opens with what it does and why, then shows its
        files with every hunk highlighted.
      </>
    ),
  },
  {
    title: "Notes pinned to the line",
    body: (
      <>
        A <Code>context</Code> note says what to hold in mind. A verified note states something the
        summary&rsquo;s author checked and how, such as a test it ran. A flag raises a concern that needs your
        decision, with a box to mark it handled.
      </>
    ),
  },
  {
    title: "Stacks read as one change",
    body: (
      <>
        A pull request that is one layer of a stack lists every layer in its sidebar, so you can
        read the plan, the change, and what builds on it in order.
      </>
    ),
  },
  {
    title: "What the reviews found so far",
    body: (
      <>
        The overview says what earlier reviews found and how each push answered them. Every head
        gets a page of its own, and boxes on each file and section track what you have read.
      </>
    ),
  },
];

function Shot({
  dark,
  light,
  alt,
  sizes,
}: {
  dark: StaticImageData;
  light: StaticImageData;
  alt: string;
  sizes: string;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-line-strong bg-surface shadow-sm">
      <Image src={light} alt={alt} sizes={sizes} className="block h-auto w-full dark:hidden" />
      <Image src={dark} alt={alt} sizes={sizes} className="hidden h-auto w-full dark:block" />
    </div>
  );
}

function DocLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-sm font-medium text-fg transition-colors hover:text-accent"
    >
      {children}
      <ArrowUpRightIcon className="h-4 w-4" />
    </a>
  );
}

export function Summaries() {
  return (
    <Section
      id="summaries"
      title="Summaries that make a pull request reviewable"
      lead={
        <>
          A diff in file order is hard to review, and harder when an agent wrote it.{" "}
          <Code>review-pr</Code> writes a guided summary of each pull request it reviews, and the
          repository&rsquo;s Projector site serves it, linked from the review. You read the change
          the way its author would explain it.
        </>
      }
    >
      <figure>
        <Shot
          light={overviewLight}
          dark={overviewDark}
          alt="The top of a Projector summary: the stack the pull request sits in, what it does, its size by kind of line, and the files it changes"
          sizes="(min-width: 1152px) 1104px, 100vw"
        />
        <figcaption className="mt-3 text-sm text-muted">
          The top of the summary of{" "}
          <a
            href={SUMMARY_EXAMPLE.page}
            target="_blank"
            rel="noreferrer"
            className="underline decoration-line-strong underline-offset-4 transition-colors hover:text-fg"
          >
            ninjudd/projector#456
          </a>
          , which added <Code>project ios</Code>. Its sidebar lists the stack it sits in: the plan
          in #450, this change, and the skill change in #457 that uses it.
        </figcaption>
      </figure>

      <div className="mt-16 grid gap-12 lg:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)] lg:gap-12">
        <div className="min-w-0">
          <ol className="space-y-6">
            {parts.map((part, index) => (
              <li key={part.title} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-2">
                <span className="mt-0.5 font-mono text-sm text-accent">{index + 1}</span>
                <div>
                  <h3 className="text-base font-semibold tracking-tight text-fg">{part.title}</h3>
                  <p className="mt-1.5 text-[15px] leading-relaxed text-muted">{part.body}</p>
                </div>
              </li>
            ))}
          </ol>
          <p className="mt-8 text-[15px] leading-relaxed text-muted">
            Run <Code>/summarize-pr</Code> on any pull request to write one yourself. The
            summary lives on a hidden ref in the repository, so it adds no branch and nothing to
            anyone&rsquo;s clone.
          </p>
          <div className="mt-6 flex flex-wrap gap-x-6 gap-y-2">
            <DocLink href={SUMMARY_EXAMPLE.page}>Open this summary</DocLink>
            <DocLink href={SUMMARY_EXAMPLE.index}>Every summary of Projector</DocLink>
            <DocLink href={DOCS.summarizePr}>summarize-pr</DocLink>
          </div>
        </div>
        <figure className="min-w-0 lg:sticky lg:top-24 lg:self-start">
          <Shot
            light={sectionLight}
            dark={sectionDark}
            alt="Part of a Projector summary's diff, with two context notes under the lines they concern"
            sizes="(min-width: 1152px) 700px, 100vw"
          />
          <figcaption className="mt-3 text-sm text-muted">
            Part of section 1 of the same summary. Each note sits under the lines it concerns.
          </figcaption>
        </figure>
      </div>
    </Section>
  );
}
