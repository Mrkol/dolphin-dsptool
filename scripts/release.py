"""Prepare immutable releases that follow Dolphin's upstream tags."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/dolphin-emu/dolphin.git"
RELEASE_TAG = re.compile(r"([0-9]{2})(0[1-9]|1[0-2])([a-z]?)")
SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
DRAFT_MARKER = "<!-- dolphin-dsptool-release -->"
BOT = {
    "GIT_AUTHOR_NAME": "github-actions[bot]",
    "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "github-actions[bot]",
    "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
}


def git(*args, repo=None, env=None, input=None):
    return subprocess.check_output(
        ["git", "-C", str(repo or ROOT), *args], text=True, input=input,
        env={**os.environ, **(env or {})}, timeout=120,
    ).strip()


def resolve_tag(listing, requested):
    tags = {}
    for line in listing.splitlines():
        sha, ref = line.split()
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or not ref.startswith("refs/tags/"):
            raise ValueError("Unexpected upstream tag listing")
        name = ref.removeprefix("refs/tags/")
        peeled = name.endswith("^{}")
        name = name.removesuffix("^{}")
        tags.setdefault(name, {})["commit" if peeled else "object"] = sha
    if requested == "latest":
        candidates = [name for name in tags if RELEASE_TAG.fullmatch(name)]
        if not candidates:
            raise ValueError("No Dolphin release tags found")
        requested = max(candidates, key=lambda tag: RELEASE_TAG.fullmatch(tag).groups())
    if not SAFE_TAG.fullmatch(requested) or requested not in tags:
        raise ValueError(f"Unknown Dolphin tag: {requested}")
    entry = tags[requested]
    return requested, entry.get("commit") or entry["object"]


def release_version(tag, revision):
    if not SAFE_TAG.fullmatch(tag):
        raise ValueError("Invalid tag")
    if not re.fullmatch(r"[1-9][0-9]{0,3}", revision):
        raise ValueError("Packaging revision must be an integer from 1 to 9999")
    return tag if revision == "1" else f"{tag}-r{revision}"


def existing_release(repository, version):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected an owner/repository name")
    url = (
        f"https://api.github.com/repos/{repository}/releases/tags/"
        + urllib.parse.quote(version, safe="")
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dolphin-dsptool-release",
    }

    def get(url):
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    try:
        return get(url)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    # The by-tag endpoint is documented for published releases; list drafts too.
    # A read-only prepare token may not see them, but the publish token can.
    page = 1
    while True:
        releases = get(
            f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}"
        )
        matches = [release for release in releases if release["tag_name"] == version]
        if len(matches) > 1:
            raise ValueError("Multiple releases use this tag; refusing to choose one")
        if matches:
            return matches[0]
        if len(releases) < 100:
            return None
        page += 1


def release_tree(base, dolphin_sha, repo=None):
    # A separate index leaves the caller's checkout and staged work untouched.
    with tempfile.TemporaryDirectory(prefix="dsptool-index-") as directory:
        env = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
        git("read-tree", base, repo=repo, env=env)
        git("update-index", "--add", "--cacheinfo", f"160000,{dolphin_sha},dolphin",
            repo=repo, env=env)
        return git("write-tree", repo=repo, env=env)


def validate_commit(commit, base, tree, repo=None):
    if git("rev-parse", f"{commit}^{{tree}}", repo=repo) != tree:
        raise ValueError("Existing release tag has a different source tree; use a new packaging revision")
    parents = git("rev-list", "--parents", "-n", "1", commit, repo=repo).split()[1:]
    if parents != [base]:
        raise ValueError("Existing release tag has a different packaging base; use a new packaging revision")


def emit(**values):
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as file:
            for key, value in values.items():
                file.write(f"{key}={value}\n")
    print(json.dumps(values, indent=2))


def validate_draft(release, commit, dolphin_sha):
    body = release.get("body") or ""
    if (DRAFT_MARKER not in body
            or f"- Packaging commit: `{commit}`" not in body
            or f"- Dolphin commit: `{dolphin_sha}`" not in body):
        raise ValueError("Existing draft does not belong to this release; it will not be modified")


def prepare(args):
    # Resolve once; downstream jobs receive the SHA rather than resolving latest again.
    tag, dolphin_sha = resolve_tag(git("ls-remote", "--tags", UPSTREAM), args.tag)
    version = release_version(tag, args.revision)
    release = existing_release(args.repository, version)
    if release and not release["draft"]:
        print(f"Already published: {release['html_url']}")
        emit(skip="true", version=version)
        return

    base = git("rev-parse", "HEAD")
    tree = release_tree(base, dolphin_sha)
    ref = f"refs/tags/{version}"
    remote_tag = git("ls-remote", "--refs", "origin", ref)
    if release and not remote_tag:
        raise ValueError("Existing draft has no release tag; it will not be modified")
    if remote_tag:
        # Recover a tag whose publication failed, but only with identical sources.
        git("fetch", "--no-tags", "origin", ref)
        commit = git("rev-parse", "FETCH_HEAD^{commit}")
        validate_commit(commit, base, tree)
    else:
        commit = git("-c", "commit.gpgSign=false", "commit-tree", tree, "-p", base, env=BOT, input=(
            f"Release DSPTool {version}\n\n"
            f"Dolphin tag: {tag}\nDolphin commit: {dolphin_sha}\n"
            f"Packaging revision: {args.revision}\n"
        ))
    if release:
        validate_draft(release, commit, dolphin_sha)
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = {
        "version": version, "dolphin_tag": tag, "dolphin_commit": dolphin_sha,
        "packaging_revision": int(args.revision), "packaging_base": base,
        "packaging_commit": commit,
    }
    (args.output / "release.json").write_text(json.dumps(metadata, indent=2) + "\n")
    git("update-ref", "refs/dsptool/release", commit)
    git("bundle", "create", str(args.output.resolve() / "release.bundle"),
        "refs/dsptool/release")
    emit(skip="false", version=version, dolphin_tag=tag, commit=commit)


def checkout(args):
    metadata = json.loads((args.input / "release.json").read_text())
    git("fetch", "--no-tags", str((args.input / "release.bundle").resolve()),
        "refs/dsptool/release")
    commit = git("rev-parse", "FETCH_HEAD^{commit}")
    if commit != metadata["packaging_commit"]:
        raise ValueError("Release bundle does not match its metadata")
    validate_commit(commit, metadata["packaging_base"],
                    release_tree(metadata["packaging_base"], metadata["dolphin_commit"]))
    entry = git("ls-tree", commit, "--", "dolphin").split()
    if entry[:3] != ["160000", "commit", metadata["dolphin_commit"]]:
        raise ValueError("Release commit does not pin the resolved Dolphin revision")
    git("-c", "submodule.recurse=false", "switch", "--detach", commit)


def publish(args):
    from package import checksums

    metadata = json.loads((args.input / "release.json").read_text())
    version = release_version(metadata["dolphin_tag"], str(metadata["packaging_revision"]))
    if version != metadata["version"]:
        raise ValueError("Release version does not match its metadata")
    if git("rev-parse", "HEAD") != metadata["packaging_commit"]:
        raise ValueError("Publish checkout does not match the tested packaging commit")
    release = existing_release(args.repository, version)
    if release and not release["draft"]:
        print(f"Already published: {release['html_url']}")
        return
    if release:
        validate_draft(release, metadata["packaging_commit"], metadata["dolphin_commit"])
    expected = {f"dsptool-{version}-{suffix}" for suffix in (
        "windows-x64.zip", "linux-x64.tar.gz", "macos-x64.tar.gz",
        "macos-arm64.tar.gz", "source.tar.gz",
    )}
    actual = {file.name for file in args.dist.iterdir()}
    if actual != expected:
        raise ValueError(f"Unexpected release assets: missing={expected - actual}, extra={actual - expected}")
    for file in args.dist.iterdir():
        directory = file.name.removesuffix(".tar.gz").removesuffix(".zip")
        member = directory + "/BUILD-INFO.json"
        if file.suffix == ".zip":
            with zipfile.ZipFile(file) as archive:
                manifest = json.loads(archive.read(member))
        else:
            with tarfile.open(file) as archive:
                manifest = json.load(archive.extractfile(member))
        for key in ("version", "dolphin_tag", "dolphin_commit", "packaging_commit"):
            if manifest.get(key) != metadata[key]:
                raise ValueError(f"{file.name}: {key} does not match the tested release")
    checksums(args.dist)
    remote = git("ls-remote", "--tags", "origin", f"refs/tags/{version}",
                 f"refs/tags/{version}^{{}}")
    if remote:
        _, commit = resolve_tag(remote, version)
        if commit != metadata["packaging_commit"]:
            raise ValueError("Remote tag already points to another packaging commit")
    else:
        git("-c", "tag.gpgSign=false", "tag", "-a", version,
            metadata["packaging_commit"], "-m", f"DSPTool for Dolphin {metadata['dolphin_tag']}",
            env=BOT)
        git("push", "origin", f"refs/tags/{version}")
    notes = args.input / "notes.md"
    notes.write_text(
        f"{DRAFT_MARKER}\n\nDSPTool built from Dolphin **{metadata['dolphin_tag']}**.\n\n"
        f"- Dolphin commit: `{metadata['dolphin_commit']}`\n"
        f"- Packaging commit: `{metadata['packaging_commit']}`\n"
        f"- Packaging revision: {metadata['packaging_revision']}\n\n"
        "Includes Windows x64, Linux x64, macOS Intel and Apple Silicon binaries, "
        "corresponding source, license notices, and SHA-256 checksums.\n",
        encoding="utf-8",
    )
    if not release:
        subprocess.run([
            "gh", "release", "create", version, "--repo", args.repository,
            "--draft", "--verify-tag", "--title", f"DSPTool {version}",
            "--notes-file", str(notes),
        ], check=True)
    # Unpublished draft assets may be replaced on retry; published releases never are.
    subprocess.run([
        "gh", "release", "upload", version, "--repo", args.repository, "--clobber",
        *map(str, sorted(args.dist.iterdir())),
    ], check=True)
    subprocess.run([
        "gh", "release", "edit", version, "--repo", args.repository, "--draft=false",
    ], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("prepare")
    plan.add_argument("--tag", default="latest")
    plan.add_argument("--revision", default="1")
    plan.add_argument("--repository", required=True)
    plan.add_argument("--output", type=Path, required=True)
    load = commands.add_parser("checkout")
    load.add_argument("--input", type=Path, required=True)
    ship = commands.add_parser("publish")
    ship.add_argument("--input", type=Path, required=True)
    ship.add_argument("--dist", type=Path, required=True)
    ship.add_argument("--repository", required=True)
    args = parser.parse_args()
    {"prepare": prepare, "checkout": checkout, "publish": publish}[args.command](args)


if __name__ == "__main__":
    main()
