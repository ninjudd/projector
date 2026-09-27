import os

# GitHub Actions sets these for every step, and Projector reads them: the site
# build skips a summary spec for any repository but GITHUB_REPOSITORY. Every
# test starts from a machine outside CI, and one that needs a value sets it.
for name in ("GITHUB_REPOSITORY", "GITHUB_ACTIONS"):
    os.environ.pop(name, None)

# Claude Code sets this for every command it runs, and project review names it in
# the signatures it writes, so a test that expects an effort sets one itself.
os.environ.pop("CLAUDE_EFFORT", None)
