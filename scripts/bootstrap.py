"""Fetch pinned submodules without first downloading their complete working trees."""

import argparse
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOLPHIN_URL = "https://github.com/dolphin-emu/dolphin.git"
FMT_URL = "https://github.com/fmtlib/fmt.git"
DOLPHIN_PATHS = [
    "Source/DSPTool", "Source/Core/Common", "Source/Core/Core/DSP",
    "Externals/fmt", "LICENSES",
]
FMT_PATHS = ["include", "src", "support/cmake"]


def git(repo, *args, capture=False):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else None


def submodule_revision(repo, path):
    entry = git(repo, "ls-files", "--stage", "--", path, capture=True)
    match = re.fullmatch(r"160000 ([0-9a-f]{40}) 0\t" + re.escape(path), entry)
    if not match:
        raise RuntimeError(f"{path} must be a staged or committed submodule in {repo}")
    return match[1]


def prepare(repo, url, revision, paths):
    if repo.exists() and not (repo / ".git").exists():
        if any(repo.iterdir()):
            raise RuntimeError(f"Refusing to initialize nonempty directory: {repo}")
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git(repo, "init")
        git(repo, "remote", "add", "origin", url)
    else:
        dirty = git(repo, "status", "--porcelain", "--untracked-files=all", capture=True)
        if dirty:
            raise RuntimeError(f"Refusing to change a dirty checkout: {repo}\n{dirty}")

    git(repo, "config", "remote.origin.promisor", "true")
    git(repo, "config", "remote.origin.partialclonefilter", "blob:none")
    git(repo, "config", "remote.origin.tagOpt", "--no-tags")
    git(repo, "config", "http.lowSpeedLimit", "1024")
    git(repo, "config", "http.lowSpeedTime", "30")
    present = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", revision + "^{commit}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    ).returncode == 0
    if not present:
        git(repo, "fetch", "--no-tags", "--depth=1", "--filter=blob:none", "origin", revision)
    git(repo, "sparse-checkout", "set", "--cone", *paths)
    git(repo, "-c", "submodule.recurse=false", "switch", "--detach", revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dolphin-revision", help="Prepare an intentional submodule update; stage dolphin afterwards")
    args = parser.parse_args()
    revision = args.dolphin_revision or submodule_revision(ROOT, "dolphin")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        parser.error("Use a full 40-character Dolphin commit hash")
    prepare(ROOT / "dolphin", DOLPHIN_URL, revision, DOLPHIN_PATHS)
    dolphin = ROOT / "dolphin"
    fmt_revision = submodule_revision(dolphin, "Externals/fmt/fmt")
    prepare(dolphin / "Externals/fmt/fmt", FMT_URL, fmt_revision, FMT_PATHS)
    git(ROOT, "submodule", "init", "--", "dolphin")
    git(dolphin, "submodule", "init", "--", "Externals/fmt/fmt")
    print(f"Dolphin: {revision}\nfmt: {fmt_revision}")


if __name__ == "__main__":
    main()
