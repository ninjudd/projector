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
  hunks: { header: string; lines: SummaryLine[] }[];
  /** The deepest project on the site whose folder holds the file; absent outside every project the site has. */
  project?: { name: string; title: string; url: string };
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

/**
 * The newest Projector review of a head, with its page on GitHub, or `unreviewed` when no
 * Projector review names the head.
 */
type ReviewStatus = { status: 'clean' | 'changes-requested'; url: string } | { status: 'unreviewed' };

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
  /** Every pull request in this one's stack, from the default branch up; absent or empty when it stands alone. */
  stack?: { number: number; title: string; url: string; current: boolean }[];
  projects?: { name: string; title: string; url: string }[];
  /** The newest Projector review of this head; absent when the build could not ask GitHub. */
  review?: ReviewStatus;
  generatedAt?: string;
}

/** A document or project file in site.json, with the route the build gave it. */
interface SitePage {
  path: string;
  title: string;
  route: string;
}

interface SiteProject {
  name: string;
  path: string;
  title: string;
  status: string;
  priority: string | null;
  owner?: string | null;
  files: SitePage[];
  reviews: number[];
}

/** A review's row on the Reviews index, in the order the index lists them: each stack's together, from the default branch up. */
interface SiteReview {
  number: number;
  name: string;
  title: string;
  heads: number;
  /** The number of the first review in this one's stack, shared by every review in it; null when no other review is in it. */
  stack: number | null;
  /** The pull request's state, and the newest Projector review of its newest head; each absent when the build could not ask GitHub. */
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

/** One entry of search.json. */
interface SearchEntry {
  route: string;
  title: string;
  kind: string;
  text: string;
}
