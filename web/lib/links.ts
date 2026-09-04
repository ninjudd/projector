export const SITE_URL = "https://projector.bot";

export const GITHUB = "https://github.com/ninjudd/projector";

export const DOCS = {
  index: `${GITHUB}/tree/main/docs`,
  cli: `${GITHUB}/blob/main/docs/cli.md`,
  plugins: `${GITHUB}/blob/main/docs/plugins.md`,
  convention: `${GITHUB}/blob/main/docs/projects/README.md`,
  issues: `${GITHUB}/issues`,
  license: `${GITHUB}/blob/main/LICENSE`,
};

export const INSTALL = {
  pipx: "pipx install git+https://github.com/ninjudd/projector.git",
  claude: [
    "claude plugin marketplace add ninjudd/projector --scope user",
    "claude plugin install projector@projector --scope user",
  ],
  codex: [
    "codex plugin marketplace add ninjudd/projector",
    "codex plugin add projector@projector",
  ],
  checkout: [
    "git clone https://github.com/ninjudd/projector.git",
    "cd projector",
    "./install.sh all",
  ],
};
