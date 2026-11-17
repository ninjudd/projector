import subprocess
import unittest

from tests.scratch import copy_of


def one_commit(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-q", "--allow-empty", "-m", "first"], check=True)


class GitMaintenanceTests(unittest.TestCase):
    def config(self, root, key):
        return subprocess.run(["git", "-C", str(root), "config", "--get", key],
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_fixture_git_runs_no_background_maintenance(self):
        # Catches the suite letting Git start detached maintenance after a commit, which
        # deletes files under objects/ while copy_of copies the template.
        root, _ = copy_of(one_commit)
        self.assertEqual(self.config(root, "maintenance.auto"), "false")
        self.assertEqual(self.config(root, "gc.auto"), "0")

    def test_a_repository_config_cannot_turn_maintenance_back_on(self):
        # Catches the setting moving to a config file, which a test repository's own
        # `maintenance.auto = true` would outrank.
        root, _ = copy_of(one_commit)
        subprocess.run(["git", "-C", str(root), "config", "maintenance.auto", "true"], check=True)
        self.assertEqual(self.config(root, "maintenance.auto"), "false")


if __name__ == "__main__":
    unittest.main()
