export const SITE_URL = "https://projector.bot";

export const GITHUB = "https://github.com/ninjudd/projector";

export const DOCS = {
  index: `${GITHUB}/tree/main/docs`,
  cli: `${GITHUB}/blob/main/docs/cli.md`,
  plugins: `${GITHUB}/blob/main/docs/plugins.md`,
  site: `${GITHUB}/blob/main/docs/site.md`,
  panel: `${GITHUB}/blob/main/docs/plugins.md#see-your-sessions-pull-requests-in-projectors-panel`,
  inbox: `${GITHUB}/blob/main/docs/cli.md#use-the-inbox`,
  console: `${GITHUB}/blob/main/docs/cli.md#use-the-console`,
  inboxPanel: `${GITHUB}/blob/main/docs/plugins.md#read-your-inbox-in-projectors-panel`,
  watch: `${GITHUB}/blob/main/docs/cli.md#watch-pull-requests`,
  worktree: `${GITHUB}/blob/main/docs/cli.md#find-a-branchs-worktree`,
  ios: `${GITHUB}/blob/main/docs/cli.md#put-an-ios-app-on-a-device`,
  requireReview: `${GITHUB}/blob/main/docs/cli.md#require-a-clean-review-before-merge`,
  work: `${GITHUB}/blob/main/skills/work/SKILL.md`,
  cleanup: `${GITHUB}/blob/main/skills/cleanup/SKILL.md`,
  message: `${GITHUB}/blob/main/skills/message/SKILL.md`,
  reviewPr: `${GITHUB}/blob/main/skills/review-pr/SKILL.md`,
  summarizePr: `${GITHUB}/blob/main/skills/summarize-pr/SKILL.md`,
  writing: `${GITHUB}/blob/main/skills/writing.md`,
  testing: `${GITHUB}/blob/main/skills/testing.md`,
  convention: `${GITHUB}/blob/main/docs/projects/README.md`,
  reviewRepo: `${GITHUB}/blob/main/skills/review-repo/SKILL.md`,
  fixPr: `${GITHUB}/blob/main/skills/fix-pr/SKILL.md`,
  issues: `${GITHUB}/issues`,
  license: `${GITHUB}/blob/main/LICENSE`,
};

// Projector's own Projector site, which its repository deploys from main.
export const PROJECTOR_SITE = "https://ninjudd.com/projector/";

// The summary the front page shows, on Projector's own site.
export const SUMMARY_EXAMPLE = {
  page: `${PROJECTOR_SITE}reviews/456/`,
  index: `${PROJECTOR_SITE}reviews/`,
};

// The example on the front page is a real project in audiofield/field.
const FIELD = "https://github.com/audiofield/field";

export const EXAMPLE = {
  repo: FIELD,
  projects: `${FIELD}/tree/main/docs/projects`,
  plan: `${FIELD}/blob/main/docs/projects/recording/readme.md`,
  pr: (number: number) => `${FIELD}/pull/${number}`,
};

export const INSTALL = {
  // Installs the CLI and the plugin for each coding agent on the machine, from
  // the newest release, without git; `project upgrade` reruns it.
  script: `curl -fsSL ${SITE_URL}/install.sh | bash`,
  upgrade: "project upgrade",
  pipx: `pipx install ${GITHUB}/archive/v0.tar.gz`,
  claude: [
    "claude plugin marketplace add ninjudd/projector --scope user",
    "claude plugin install projector@projector --scope user",
  ],
  codex: [
    "codex plugin marketplace add ninjudd/projector",
    "codex plugin add projector@projector",
  ],
};
