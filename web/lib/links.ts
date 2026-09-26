export const SITE_URL = "https://projector.bot";

export const GITHUB = "https://github.com/ninjudd/projector";

export const DOCS = {
  index: `${GITHUB}/tree/main/docs`,
  cli: `${GITHUB}/blob/main/docs/cli.md`,
  plugins: `${GITHUB}/blob/main/docs/plugins.md`,
  site: `${GITHUB}/blob/main/docs/site.md`,
  convention: `${GITHUB}/blob/main/docs/projects/README.md`,
  reviewLoop: `${GITHUB}/blob/main/skills/start-review-loop/SKILL.md`,
  fixLoop: `${GITHUB}/blob/main/skills/start-fix-loop/SKILL.md`,
  issues: `${GITHUB}/issues`,
  license: `${GITHUB}/blob/main/LICENSE`,
};

// Projector's own Projector site, which its repository deploys from main.
export const PROJECTOR_SITE = "https://ninjudd.com/projector/";

// The example on the front page is a real project in ninjudd/trip.
const TRIP = "https://github.com/ninjudd/trip";

export const EXAMPLE = {
  repo: TRIP,
  projects: `${TRIP}/tree/main/docs/projects`,
  plan: `${TRIP}/blob/main/docs/projects/session-switcher/readme.md`,
  pr: (number: number) => `${TRIP}/pull/${number}`,
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
