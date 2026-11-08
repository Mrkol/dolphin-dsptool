"""Create tested binary archives and a self-contained corresponding source archive."""

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATHS = [
    "CMakeLists.txt", "LICENSE", "README.md", "src", "scripts", "tests",
    ".github", ".gitmodules", ".gitignore", ".gitattributes",
]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def copy(source, destination):
    if source.is_dir():
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def archive(directory, output, windows=False):
    if windows:
        path = output / (directory.name + ".zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(directory.rglob("*")):
                if file.is_file():
                    bundle.write(file, file.relative_to(directory.parent))
    else:
        path = output / (directory.name + ".tar.gz")
        with tarfile.open(path, "w:gz") as bundle:
            bundle.add(directory, arcname=directory.name)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dolphin-tag", default="")
    parser.add_argument("--platform", required=True, choices=["windows-x64", "linux-x64", "macos-x64", "macos-arm64"])
    parser.add_argument("--prefix", type=Path, default=ROOT / "stage")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--source", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", args.version):
        parser.error("Version must be a filename-safe tag or identifier")
    args.output.mkdir(parents=True, exist_ok=True)
    dolphin = ROOT / "dolphin"
    packaging_commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"],
        capture_output=True, text=True,
    )
    manifest = {
        "version": args.version,
        "dolphin_tag": args.dolphin_tag or None,
        "platform": args.platform,
        "packaging_commit": packaging_commit.stdout.strip() if packaging_commit.returncode == 0 else "uncommitted",
        "dolphin_commit": git(dolphin, "rev-parse", "HEAD"),
        "fmt_commit": git(dolphin / "Externals/fmt/fmt", "rev-parse", "HEAD"),
        "build_host": platform.platform(),
    }
    windows = args.platform.startswith("windows")
    filename = "dsptool.exe" if windows else "dsptool"
    with tempfile.TemporaryDirectory(prefix="dsptool-package-") as temporary:
        base = Path(temporary)
        package = base / f"dsptool-{args.version}-{args.platform}"
        (package / "bin").mkdir(parents=True)
        copy(args.prefix / "bin" / filename, package / "bin" / filename)
        copy(ROOT / "README.md", package / "README.md")
        copy(ROOT / "LICENSE", package / "LICENSE")
        copy(dolphin / "COPYING", package / "licenses/Dolphin-COPYING")
        copy(dolphin / "LICENSES", package / "licenses/Dolphin")
        copy(dolphin / "Externals/fmt/fmt/LICENSE", package / "licenses/fmt-LICENSE")
        (package / "BUILD-INFO.json").write_text(json.dumps(manifest, indent=2) + "\n")
        binary_archive = archive(package, args.output, windows)
        # Test the distributed bytes, including executable permissions in tarballs.
        unpack = base / "unpacked"
        shutil.unpack_archive(binary_archive, unpack)
        subprocess.run([
            sys.executable, str(ROOT / "tests/smoke.py"), str(unpack / package.name / "bin" / filename),
        ], check=True)
        if args.source:
            source = base / f"dsptool-{args.version}-source"
            source.mkdir()
            for path in WRAPPER_PATHS:
                copy(ROOT / path, source / path)
            # Include every populated upstream source/header, but no Git databases.
            # CMake can build this archive without Git, a network, or bootstrap.
            copy(dolphin, source / "dolphin")
            source_manifest = dict(manifest)
            source_manifest.pop("platform")
            (source / "BUILD-INFO.json").write_text(json.dumps(source_manifest, indent=2) + "\n")
            archive(source, args.output)
    print(f"Archives written to {args.output}")


def checksums(directory):
    files = sorted(path for path in directory.iterdir() if path.name.endswith((".zip", ".tar.gz")))
    if not files:
        raise RuntimeError("No release archives found")
    text = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in files)
    (directory / "SHA256SUMS").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
