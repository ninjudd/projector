import atexit
import os
import shutil
import tempfile

# Tests make scratch repositories with tempfile.mkdtemp and do not remove them.
# One run leaves about 19,000 inodes, so review loops that run the suite in
# parallel can fill a tmpfs /tmp. Every temporary path a run makes, including
# its subprocesses' paths, goes under one root that the run removes at exit.
root = tempfile.mkdtemp(prefix="projector-tests-")
tempfile.tempdir = root
os.environ["TMPDIR"] = root
atexit.register(shutil.rmtree, root, ignore_errors=True)

# GitHub Actions sets these for every step, and Projector reads them: the site
# build skips a summary for any repository but GITHUB_REPOSITORY. Every
# test starts from a machine outside CI, and one that needs a value sets it.
for name in ("GITHUB_REPOSITORY", "GITHUB_ACTIONS"):
    os.environ.pop(name, None)

# Claude Code sets this for every command it runs, and project review names it in
# the signatures it writes, so a test that expects an effort sets one itself.
os.environ.pop("CLAUDE_EFFORT", None)
