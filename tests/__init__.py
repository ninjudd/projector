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

# Git runs automatic maintenance in a detached background process after a
# commit, fetch, or merge, and that process can still be adding and removing
# files under `objects/` when `scratch.copy_of` copies a fixture, which fails the
# copy. Command-line configuration outranks every config file, so no test's
# repository can turn it back on.
os.environ.update({"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "maintenance.auto", "GIT_CONFIG_VALUE_0": "false",
                   "GIT_CONFIG_KEY_1": "gc.auto", "GIT_CONFIG_VALUE_1": "0"})

# Claude Code sets this for every command it runs, and project review names it in
# the signatures it writes, so a test that expects an effort sets one itself. The
# review action sets the version a review names, and a review's gate can run
# these tests on a runner, so a test that expects one sets it itself.
os.environ.pop("CLAUDE_EFFORT", None)
os.environ.pop("PROJECTOR_VERSION", None)
