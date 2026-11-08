# Dolphin DSPTool releases

⚠ WARNING: Repo entirely vibe-coded! I'm not a devops, I don't care! ⚠

Standalone builds of Dolphin's DSP assembler/disassembler for GameCube and Wii research.
Dolphin is pinned as a Git submodule; this repository supplies the focused build and release automation.
The instruction tables, assembler, disassembler, and command-line interface come directly from Dolphin.
The only local C++ adapter sends application logs and alerts to the console.

## Downloads

Tagged releases contain Windows x64, Linux x64, macOS Intel, and macOS Apple Silicon builds.
Windows uses the static MSVC runtime; fmt is linked statically everywhere.
Linux builds on Ubuntu 22.04 and requires glibc 2.35 or newer, with the C++ runtime linked statically.
macOS targets version 13.0 or newer; the executables are not Developer ID signed or notarized.
Each release includes `SHA256SUMS`, license notices, build revisions, and a corresponding source archive.
Development builds are available as workflow artifacts.

```sh
dsptool --help
dsptool -d -o microcode.txt microcode.bin
dsptool -o microcode.bin microcode.ds
```

DSP addresses and instructions use 16-bit words, and binary files use big-endian byte order.
Upstream's default disassembly includes addresses and hex words, so it is not directly reassemblable.
The upstream CLI is preserved, including its current limitations around file I/O error reporting.

## Build from Git

Requires Git with partial-clone and sparse-checkout support, Python 3.10+, CMake 3.24+, and a C++23 compiler (GCC 12+, current MSVC, or current Apple Clang).
Clone **without** `--recurse-submodules`, then run the bootstrap:

```sh
git clone https://github.com/Mrkol/dolphin-dsptool.git
cd dolphin-dsptool
python scripts/bootstrap.py
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel
ctest --test-dir build -C Release --output-on-failure
cmake --install build --config Release --prefix stage
```

On Windows, run from a Visual Studio developer terminal or select the installed Visual Studio generator explicitly.
On Ubuntu 22.04, install `g++-12` and configure with `-DCMAKE_CXX_COMPILER=g++-12`.
The installed executable is in `stage/bin`.

The bootstrap fetches the recorded commit with `--depth=1 --filter=blob:none`, configures sparse paths before checkout, and fetches only the pinned fmt dependency.
It never initializes Dolphin's other submodules.
Sparse settings are local Git configuration, so contributors and CI must use this script to get the small download.
Existing uncommitted dependency changes cause it to stop rather than overwrite them.
Submodules retain embedded Git directories to avoid relocating sparse-checkout worktree configuration.

## Build from a release source archive

Extract the `*-source.tar.gz` archive and run the CMake commands above, skipping Git clone and bootstrap.
It includes all selected Dolphin and fmt sources and needs no Git metadata or dependency downloads.
CI also rebuilds and tests this archive independently.
GitHub's automatically generated source archives do not include submodules; use the explicitly attached source asset.

## Update Dolphin

Choose and review a full upstream commit hash, then run:

```sh
python scripts/bootstrap.py --dolphin-revision FULL_40_CHARACTER_COMMIT_HASH
git add dolphin
```

Commit the submodule update after building and testing it.
The fmt version follows the revision pinned by Dolphin.
Changes to upstream dependencies may require adjusting the sparse paths or the CMake source list.

## Publish

Open **Actions → Release DSPTool → Run workflow** on the default branch.
Leave **Dolphin tag** set to `latest` and **Packaging revision** set to `1` for a normal release.
The workflow selects the newest upstream `YYMM` release tag, including lettered hotfixes such as `2609b`.
You can enter a specific upstream tag instead to release an older version.
Dolphin uses Git tags rather than GitHub Releases, so discovery reads upstream Git refs directly.

DSPTool release tags mirror Dolphin exactly: Dolphin `2609b` produces DSPTool `2609b`.
For a packaging fix to the same upstream version, select revision `2` to publish `2609b-r2`, then `3` for `2609b-r3`, and so on.
Packaging revisions are normal releases, not prereleases.
An already-published version is a successful no-op; published assets are never replaced.

The workflow resolves the upstream tag to a commit once and creates a packaging commit with that exact Dolphin submodule revision.
It shares that commit through a Git bundle so all four platform builds test the same source.
After every build, executable archive test, and standalone source rebuild passes, it pushes the release tag and publishes the archives and checksums.
The release commit is reachable through its tag; the workflow does not update the default branch.
Both packaging and upstream commit hashes are recorded in release notes and archive metadata.
If publication fails after the tag is pushed, retry with the same inputs and packaging base to resume; a conflicting tag requires a new packaging revision.
Assets are uploaded to a draft before publication, so a failed upload cannot expose a partial release.
Retries can resume a draft created by this workflow; unrelated drafts are left alone.

Pushes, pull requests, and manual runs of **CI** only build and test the committed submodule revision.
Pushing a tag does not trigger publication.
The manual release workflow builds and publishes within the same run using the automatic `GITHUB_TOKEN`; no personal token is required.

## License

The wrapper code is GPL-2.0-or-later, matching the selected Dolphin sources.
Upstream copyright and per-file license notices remain in the submodule and source archive.
fmt retains its own license, included in every binary archive.
