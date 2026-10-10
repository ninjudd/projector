// The libraries the site's pages load from cdnjs before its own scripts, and the
// shapes of the data files the build writes beside them.

interface Window {
  DOMPurify: { sanitize(html: string): string };
  marked: { parse(markdown: string): string };
  hljs?: {
    highlightElement(element: Element): void;
    highlight(text: string, options: { language: string; ignoreIllegals: boolean }): { value: string };
    getLanguage(name: string): unknown;
  };
  /** The localStorage prefix a rendered summary keeps its checkboxes under. */
  __SUMMARY_KEY__?: string;
}

/** A diff line: its kind (a, d, c, or m for a meta line), old and new numbers, and text. */
type SummaryLine = [string, number | null, number | null, string];

/** A hunk: its header line, the start and line count of each side the header names, and its lines. */
interface SummaryHunk {
  header: string;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: SummaryLine[];
}

/**
 * A run of head lines a file's hunks leave hidden: new-side lines `start` to `end`, above hunk `before`, or
 * below the last hunk when `before` is the number of hunks. New line `start` is old line `oldStart`.
 */
interface SummaryGap {
  before: number;
  start: number;
  end: number;
  oldStart: number;
}

/** The head's copy of a file, served at `url` with `lines` lines, and the gaps its hunks leave. */
interface SummaryContext {
  url: string;
  lines: number;
  gaps: SummaryGap[];
}

interface SummaryFile {
  path: string;
  id: string;
  anchor: string;
  lang: string;
  kind: string;
  new?: boolean;
  deleted?: boolean;
  adds: number;
  dels: number;
  hunks: SummaryHunk[];
  /** The deepest project on the site whose folder holds the file; absent outside every project the site has. */
  project?: { name: string; title: string; url: string };
  /** What lets the page reveal the lines around the hunks; absent where the summary stored no copy of the file. */
  context?: SummaryContext;
}

/** A review note: context to hold, an invariant to verify, or a flag that needs a decision. A note with a line sits under that line of its file's diff, on the new side unless `side` is "old". */
interface SummaryCheck {
  kind: string;
  text: string;
  line?: number;
  side?: string;
}

interface SummaryGroup {
  id: string;
  title: string;
  kicker?: string;
  intro?: string[];
  checks?: SummaryCheck[];
  files: { path: string; collapsed?: boolean | null; note?: string; checks?: SummaryCheck[] }[];
}

/** A pull request's state when the site was built. */
type PullState = 'open' | 'merged' | 'closed';

/** The head of the full review a clean incremental review builds on, and that review's page, or null when the build found none. */
interface IncrementalFrom { head: string; url: string | null }

/** One model's newest Projector review of a head, with its page on GitHub. */
type ModelStatus =
  | { model: string; status: 'clean'; url: string; incrementalFrom?: IncrementalFrom }
  | { model: string; status: 'changes-requested'; url: string };

/**
 * A head's status from the newest Projector review each model gave it, or `unreviewed` when no
 * Projector review names the head. It is `changes-requested` when any model's is, with `url` the
 * newest such review, and `clean` otherwise. `models` lists each model, changes requested first.
 * A clean status carries `incrementalFrom` when its one model's review is incremental.
 */
type ReviewStatus =
  | { status: 'clean'; url: string; models: ModelStatus[]; incrementalFrom?: IncrementalFrom }
  | { status: 'changes-requested'; url: string; models: ModelStatus[] }
  | { status: 'unreviewed' };

/** What `statusHtml` draws a verdict from: a head's status, or one model's. */
type Verdict = { status: 'clean' | 'changes-requested'; url: string } | { status: 'unreviewed' };

/** A stack segment's header: the project its pull requests carry out, with its page, or the stack's title alone. */
interface StackHeader {
  title: string;
  project?: string;
  url?: string;
}

/** A run of a stack's pull requests, bottom first, that carry out one project, or a whole stack that carries none. */
interface StackSegment {
  header: StackHeader;
  members: number[];
}

/**
 * A pull request's row in a stack: its title, or "" when it has no summary on the site, and where the row links,
 * its review or else its page on GitHub. `current` marks the summary page's own pull request.
 */
interface StackRow {
  number: number;
  title: string;
  url: string;
  current?: boolean;
}

/**
 * A sidebar, as `sideNavHtml` draws it on a summary page and on a project page: a landmark named `label`; each of
 * `stacks`, its segments with a row for each member; `empty` in their place when there is none; then `middle`, and
 * the footer row with the credit and `foot`. A header that names the project `current` is drawn as the page you
 * are on, and "" names none.
 */
interface SideNav {
  label: string;
  stacks: { segments: StackSegment[]; rows: StackRow[] }[];
  current: string;
  empty: string;
  middle: string;
  foot: string;
}

/** A summary page's data: the summary checked against its diff by the build. */
interface SummaryData {
  name?: string;
  /**
   * `base` is the merge base the diff is taken from. `currentBaseRef` is the base branch GitHub reported when
   * the site was built, or null once that branch was deleted; it and `state` are absent when the build could
   * not ask GitHub.
   */
  pr: {
    repo: string; number: number; title: string; head: string; base?: string; headRef?: string; baseRef?: string;
    state?: PullState; currentBaseRef?: string | null;
  };
  /** The repository's default branch when the site was built, where a merged pull request's files now are once the branch it merged into is gone. */
  defaultBranch?: string;
  files: SummaryFile[];
  groups: SummaryGroup[];
  overview?: { summary?: string[]; cards?: { id?: string; title: string; html: string }[] };
  stats: { files: number; adds: number; dels: number; hand: number; test: number; generated: number; docs: number };
  /** Every published head of the pull request, newest first; `at` is when it was published, in Unix seconds. */
  heads?: { head: string; url: string; current: boolean; at?: number }[];
  /**
   * Every pull request in this one's stack, bottom first; absent or empty when it stands alone. One without a
   * summary on the site has no title, and its `url` is its page on GitHub.
   */
  stack?: StackRow[];
  /** The stack's segments, bottom first, every member of the stack in one. A pull request alone has one. */
  segments: StackSegment[];
  /** The header of the segment this pull request is in. */
  header: StackHeader;
  /** This head's status across the models that reviewed it, as `ReviewStatus` describes; absent when the build could not ask GitHub. */
  review?: ReviewStatus;
  generatedAt?: string;
}

/** A document or project file in site.json, with the route the build gave it. */
interface SitePage {
  path: string;
  title: string;
  route: string;
}

/**
 * The open pull request whose newest summary adds a proposed project's plan, the full SHA of that summary's head,
 * and the other open pull requests whose newest summaries add a plan of the same name, lowest first.
 */
interface Proposal {
  number: number;
  head: string;
  also: number[];
}

interface SiteProject {
  name: string;
  path: string;
  title: string;
  status: string;
  priority: string | null;
  owner?: string | null;
  files: SitePage[];
  /** Every stack that holds a segment this project heads, newest first, each as its pages carry its segments. */
  stacks: StackSegment[][];
  /** Present only on a project an open pull request proposes, which is not in the checkout the site was built from. */
  proposed?: Proposal;
}

/** A review's row on the Reviews index, in the order the index lists them: each stack's together, from the default branch up. */
interface SiteReview {
  number: number;
  title: string;
  /** The header of the stack segment this review is in. */
  header: StackHeader;
  heads: number;
  /** The number of the first review in this one's stack, shared by every review in it; null when no other review is in it. */
  stack: number | null;
  /** The pull request's state, and its newest head's status across the models that reviewed it, as `ReviewStatus` describes; each absent when the build could not ask GitHub. */
  state?: PullState;
  review?: ReviewStatus;
  /** When the newest head was published, as an ISO 8601 time in UTC. */
  updated: string;
}

/** site.json: everything the build describes, from which the page draws every view. */
interface SiteData {
  repo: string;
  base: string;
  branch: string;
  content: string;
  readme: string | null;
  docs: SitePage[];
  projectsDir: string | null;
  projects: SiteProject[];
  projectsReadme: string | null;
  files: string[];
  reviews: SiteReview[];
}

/** One entry of search.json; `proposed` is the pull request that proposes the page's project, if one does. */
interface SearchEntry {
  route: string;
  title: string;
  kind: string;
  text: string;
  proposed?: number;
}
