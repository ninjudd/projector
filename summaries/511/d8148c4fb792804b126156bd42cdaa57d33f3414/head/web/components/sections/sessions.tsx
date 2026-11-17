import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const parts: { title: string; invoke: string; body: ReactNode; href: string; link: string }[] = [
  {
    title: "Work while you are away",
    invoke: "/work finish the retry plan",
    body: (
      <>
        The session makes each decision that has a reasonable answer itself. It posts the ones you
        must review to your inbox, with the answer it recommends, and builds what they do not
        block. It marks each pull request ready once its work is done, and fixes what each review
        finds. It still opens pull requests and never merges them, and it asks before an action
        that is hard to undo.
      </>
    ),
    href: DOCS.work,
    link: "work",
  },
  {
    title: "Let sessions talk to each other",
    invoke: "/message ask the test writer which error a truncated header should raise",
    body: (
      <>
        A session registers under a name and a summary of what it is doing. Ask one session to
        message another, and it finds that session by its name or summary and sends the message.
        The watch daemon wakes the recipient with it. Between your machines, the messages travel
        encrypted through hidden refs of a repository both machines work in, so only your machines
        can read or write them. A message from another agent carries none of your authority.
      </>
    ),
    href: DOCS.message,
    link: "message",
  },
  {
    title: "Clean up when you are done",
    invoke: "/cleanup",
    body: (
      <>
        The session checks each thing you asked for against evidence that it was delivered, such
        as a pull request URL. For anything left, it asks whether to do it now, open an issue, or
        leave it. Then it releases its subscriptions, inbox items, and worktrees, and tells you when
        it is safe to shut down.
      </>
    ),
    href: DOCS.cleanup,
    link: "cleanup",
  },
];

export function Sessions() {
  return (
    <Section
      id="sessions"
      title="Leave sessions working"
      lead={
        <>
          A session keeps fixing and reviewing its pull requests with no loop for you to start.
          Three more tools let it work unattended, reach your other sessions, and finish cleanly.
        </>
      }
      size="tight"
    >
      <ol className="grid gap-8 lg:grid-cols-3">
        {parts.map((part) => (
          <li key={part.title} className="flex min-w-0 flex-col border-t border-line-strong pt-5">
            <h3 className="text-lg font-semibold tracking-tight text-fg">{part.title}</h3>
            <pre className="mt-4 whitespace-pre-wrap break-words rounded-lg border border-line bg-surface px-4 py-3 font-mono text-[13px] leading-6 text-fg">
              {part.invoke}
            </pre>
            <p className="mt-4 text-[15px] leading-relaxed text-muted">{part.body}</p>
            <a
              href={part.href}
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex items-center gap-1 font-mono text-sm text-muted transition-colors hover:text-fg"
            >
              {part.link}
              <ArrowUpRightIcon className="h-3.5 w-3.5" />
            </a>
          </li>
        ))}
      </ol>
      <p className="mt-10 max-w-2xl text-[15px] leading-relaxed text-muted">
        In Codex, the same skills are <Code>$work</Code>, <Code>$message</Code>, and{" "}
        <Code>$cleanup</Code>.
      </p>
    </Section>
  );
}
