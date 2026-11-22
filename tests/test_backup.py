"""Nightly encrypted backup (backup.py): what goes in, and that the archive is reproducible."""

import io
import gzip
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automations import backup


class BackupTests(unittest.TestCase):
    def test_archive_is_the_same_for_the_same_contents(self):
        d = Path(tempfile.mkdtemp())
        (d / "a").write_text("x")
        one = backup.archive([(d / "a", "a")], {"p.txt": b"pkg"})
        (d / "a").touch()                        # a new mtime changes nothing
        self.assertEqual(one, backup.archive([(d / "a", "a")], {"p.txt": b"pkg"}))
        names = tarfile.open(fileobj=io.BytesIO(gzip.decompress(one))).getnames()
        self.assertEqual(names, ["a", "p.txt"])

    def test_sources_leave_out_logs_and_message_copies(self):
        root = Path(tempfile.mkdtemp()) / "my-secretary"          # names follow the folder's own name
        for rel in ("private.toml", "rules.toml", "var/groups/assign.json", "var/groups/messages.jsonl",
                    "var/automations.log", "var/school/items.jsonl", "var/school/messages.jsonl"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x")
        with mock.patch.object(backup, "ROOT", root), mock.patch.object(backup, "VAR", root / "var"), \
             mock.patch.object(backup, "HOME", root / "home"), \
             mock.patch.object(backup, "MEMORY", root / "none"), mock.patch.object(backup, "TASKER", root / "none"):
            names = [n for _, n in backup.sources()]
        self.assertEqual(sorted(names), ["my-secretary/private.toml", "my-secretary/rules.toml",
                                         "my-secretary/var/groups/assign.json",
                                         "my-secretary/var/school/items.jsonl"])


class NeverPublicTests(unittest.TestCase):
    """The backup goes only to the private repo in the `backup` remote, never origin or a public repo."""

    def git(self, url):
        return mock.patch.object(backup, "_git", return_value=mock.Mock(stdout=url + "\n", returncode=0))

    def test_no_backup_remote_no_push(self):
        with self.git(""):
            self.assertIn("no git remote named 'backup'", backup._cannot_push())

    def test_a_public_repo_is_refused(self):
        gh = mock.Mock(returncode=0, stdout="false\n")
        with self.git("https://github.com/someone/automations.git"), \
             mock.patch.object(backup.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(backup.subprocess, "run", return_value=gh) as run:
            self.assertEqual(backup._cannot_push(), "someone/automations is a public repo")
        self.assertIn("repos/someone/automations", run.call_args.args[0])

    def test_a_private_repo_is_fine(self):
        with self.git("git@github.com:someone/private-backup.git"), \
             mock.patch.object(backup.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(backup.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="true\n")):
            self.assertEqual(backup._cannot_push(), "")

    def test_push_goes_to_the_backup_remote_only(self):
        self.assertEqual(backup.REMOTE, "backup")
        src = Path(backup.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"origin"', src)


if __name__ == "__main__":
    unittest.main()
