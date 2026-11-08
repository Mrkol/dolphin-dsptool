"""Verify tag selection, immutable versioning, and release commit handoff."""

import argparse
import importlib.util
import json
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "release", Path(__file__).resolve().parents[1] / "scripts/release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class TagTests(unittest.TestCase):
    def test_release_lookup_finds_drafts_when_published_endpoint_returns_404(self):
        draft = {"tag_name": "2609b", "draft": True, "body": "draft"}
        missing = urllib.error.HTTPError("https://example.com", 404, "Not found", {}, None)
        with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), \
                patch.object(release.urllib.request, "urlopen", side_effect=[
                    missing, io.BytesIO(json.dumps([draft]).encode()),
                ]):
            self.assertEqual(release.existing_release("owner/repo", "2609b"), draft)

    def test_latest_includes_hotfixes_and_peels_annotated_tags(self):
        listing = "\n".join([
            "1" * 40 + "\trefs/tags/2603",
            "2" * 40 + "\trefs/tags/2609",
            "3" * 40 + "\trefs/tags/2609b",
            "4" * 40 + "\trefs/tags/2609b^{}",
            "5" * 40 + "\trefs/tags/2609a",
            "6" * 40 + "\trefs/tags/5.0-99999",
            "7" * 40 + "\trefs/tags/2613",
        ])
        self.assertEqual(release.resolve_tag(listing, "latest"), ("2609b", "4" * 40))
        self.assertEqual(release.resolve_tag(listing, "2603"), ("2603", "1" * 40))
        with self.assertRaises(ValueError):
            release.resolve_tag(listing, "does-not-exist")

    def test_revision_preserves_upstream_version(self):
        self.assertEqual(release.release_version("2609b", "1"), "2609b")
        self.assertEqual(release.release_version("2609b", "2"), "2609b-r2")
        for revision in ("0", "-1", "1.0", "2\nbad", "10000"):
            with self.assertRaises(ValueError):
                release.release_version("2609b", revision)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dsptool-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "packaging"
        self.repo.mkdir()
        self.root_patch = patch.object(release, "ROOT", self.repo)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.environment = patch.dict(os.environ, release.BOT)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        release.git("init")
        (self.repo / "README.md").write_text("Packaging fixture\n")
        release.git("add", "README.md")
        release.git("-c", "commit.gpgSign=false", "commit", "-m", "Initial fixture")
        self.base = release.git("rev-parse", "HEAD")
        self.dolphin_sha = "a" * 40
        self.output = self.root / "prepared"
        self.github_output = patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.root / "outputs")})
        self.github_output.start()
        self.addCleanup(self.github_output.stop)
        self.args = argparse.Namespace(
            tag="latest", revision="1", repository="owner/repo", output=self.output,
        )

    def prepare(self, remote_tag="", draft=None):
        real_git = release.git

        def fake_remote(*args, **kwargs):
            if args[:2] == ("ls-remote", "--tags"):
                return self.dolphin_sha + "\trefs/tags/2609b"
            if args[:2] == ("ls-remote", "--refs"):
                return remote_tag
            if args[:3] == ("fetch", "--no-tags", "origin"):
                return real_git("fetch", "--no-tags", str(self.repo), args[3], **kwargs)
            return real_git(*args, **kwargs)

        with patch.object(release, "git", side_effect=fake_remote), \
                patch.object(release, "existing_release", return_value=draft):
            release.prepare(self.args)
        return json.loads((self.output / "release.json").read_text())

    def test_commit_bundle_pins_source_without_changing_branch_or_index(self):
        index_before = release.git("ls-files", "--stage")
        metadata = self.prepare()
        self.assertEqual(release.git("rev-parse", "HEAD"), self.base)
        self.assertEqual(release.git("ls-files", "--stage"), index_before)
        self.assertEqual(release.git("status", "--porcelain"), "")
        target = self.root / "builder"
        subprocess.run(["git", "clone", "--no-checkout", str(self.repo), str(target)], check=True)
        with patch.object(release, "ROOT", target):
            release.checkout(argparse.Namespace(input=self.output))
            self.assertEqual(release.git("rev-parse", "HEAD"), metadata["packaging_commit"])
            self.assertIn(self.dolphin_sha, release.git("ls-files", "--stage", "dolphin"))

    def test_published_release_is_noop(self):
        with patch.object(release, "git", return_value=self.dolphin_sha + "\trefs/tags/2609b"), \
                patch.object(release, "existing_release", return_value={
                    "draft": False, "html_url": "https://example.com/release",
                }), patch.object(release, "release_tree") as tree:
            release.prepare(self.args)
        tree.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_recovery_rejects_different_packaging_tree_or_parent(self):
        metadata = self.prepare()
        tree = release.release_tree(self.base, self.dolphin_sha)
        release.validate_commit(metadata["packaging_commit"], self.base, tree)
        with self.assertRaises(ValueError):
            release.validate_commit(metadata["packaging_commit"], self.base,
                                    release.release_tree(self.base, "b" * 40))
        with self.assertRaises(ValueError):
            release.validate_commit(metadata["packaging_commit"], "b" * 40, tree)

    def test_checkout_rejects_wrong_bundle_commit(self):
        self.prepare()
        metadata = json.loads((self.output / "release.json").read_text())
        metadata["packaging_commit"] = self.base
        (self.output / "release.json").write_text(json.dumps(metadata))
        with self.assertRaises(ValueError):
            release.checkout(argparse.Namespace(input=self.output))

    def test_retry_reuses_matching_tag_after_failed_publication(self):
        first = self.prepare()
        release.git("-c", "tag.gpgSign=false", "tag", "2609b", first["packaging_commit"])
        second = self.prepare(remote_tag=first["packaging_commit"] + "\trefs/tags/2609b")
        self.assertEqual(first, second)

    def test_retry_accepts_own_draft_and_rejects_unrelated_draft(self):
        first = self.prepare()
        release.git("-c", "tag.gpgSign=false", "tag", "2609b", first["packaging_commit"])
        draft = {"draft": True, "body": (
            release.DRAFT_MARKER + "\n"
            f"- Packaging commit: `{first['packaging_commit']}`\n"
            f"- Dolphin commit: `{first['dolphin_commit']}`\n"
        )}
        remote = first["packaging_commit"] + "\trefs/tags/2609b"
        self.assertEqual(self.prepare(remote_tag=remote, draft=draft), first)
        with self.assertRaisesRegex(ValueError, "will not be modified"):
            self.prepare(remote_tag=remote, draft={"draft": True, "body": "Unrelated draft"})

    def release_assets(self, metadata):
        dist = self.root / "dist"
        dist.mkdir()
        for suffix in ("windows-x64.zip", "linux-x64.tar.gz", "macos-x64.tar.gz",
                       "macos-arm64.tar.gz", "source.tar.gz"):
            name = "dsptool-2609b-" + suffix
            member = name.removesuffix(".tar.gz").removesuffix(".zip") + "/BUILD-INFO.json"
            content = json.dumps(metadata).encode()
            if name.endswith(".zip"):
                with zipfile.ZipFile(dist / name, "w") as archive:
                    archive.writestr(member, content)
            else:
                with tarfile.open(dist / name, "w:gz") as archive:
                    info = tarfile.TarInfo(member)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
        return dist

    def test_publish_checks_provenance_before_pushing_and_creates_stable_release(self):
        metadata = self.prepare()
        release.checkout(argparse.Namespace(input=self.output))
        dist = self.release_assets(metadata)
        real_git = release.git
        pushes = []
        real_run = subprocess.run
        gh_commands = []

        def fake_gh(command, *args, **kwargs):
            if command[0] == "gh":
                gh_commands.append(command)
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        def fake_push(*args, **kwargs):
            if args[:2] == ("ls-remote", "--tags"):
                return ""
            if args[0] == "push":
                pushes.append(args)
                return ""
            return real_git(*args, **kwargs)

        with patch.object(release, "existing_release", return_value=None), \
                patch.object(release, "git", side_effect=fake_push), \
                patch.object(release.subprocess, "run", side_effect=fake_gh):
            release.publish(argparse.Namespace(input=self.output, dist=dist, repository="owner/repo"))
        self.assertEqual(pushes, [("push", "origin", "refs/tags/2609b")])
        self.assertEqual(len(gh_commands), 3)
        command = gh_commands[0]
        self.assertEqual(command[:4], ["gh", "release", "create", "2609b"])
        self.assertIn("--draft", command)
        self.assertNotIn("--prerelease", command)
        self.assertEqual(gh_commands[1][:4], ["gh", "release", "upload", "2609b"])
        self.assertEqual(gh_commands[2][:4], ["gh", "release", "edit", "2609b"])
        self.assertIn("--draft=false", gh_commands[2])
        self.assertTrue((dist / "SHA256SUMS").is_file())

    def test_publish_refuses_archives_from_other_commits(self):
        metadata = self.prepare()
        release.checkout(argparse.Namespace(input=self.output))
        wrong = dict(metadata, dolphin_commit="b" * 40)
        dist = self.release_assets(wrong)
        with patch.object(release, "existing_release", return_value=None), \
                patch.object(release, "git", return_value=metadata["packaging_commit"]), \
                patch.object(release.subprocess, "run") as gh:
            with self.assertRaisesRegex(ValueError, "does not match"):
                release.publish(argparse.Namespace(input=self.output, dist=dist, repository="owner/repo"))
        gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
