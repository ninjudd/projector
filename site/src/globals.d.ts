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
}

interface SummaryGroup {
  id: string;
  title: string;
  kicker?: string;
  intro?: string[];
  concepts?: string[];
  checks?: { kind: string; text: string }[];
  files: { path: string; collapsed?: boolean | null; note?: string }[];
}

/** A summary page's data: the spec checked against its diff by the build. */
interface SummaryData {
  name?: string;
  pr: { repo: string; number: number; title: string; head: string; baseRef?: string };
  files: SummaryFile[];
  groups: SummaryGroup[];
  overview?: { summary?: string[]; cards?: { id?: string; title: string; html: string }[] };
  stats: { files: number; adds: number; dels: number; hand: number; test: number; generated: number; docs: number };
  heads?: { head: string; url: string; current: boolean }[];
  projects?: { name: string; title: string; url: string }[];
  indexUrl?: string;
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

interface SiteReview {
  number: number;
  name: string;
  title: string;
  head: string;
  heads: number;
  projects: string[];
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
