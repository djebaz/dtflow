# Version bump and release

Use this guide for repository releases; the [roadmap](../plans/releases.md) owns scope and acceptance. Planned version numbers are not shipped versions.

## Prepare the release PR

Start from clean, up-to-date main. Inspect current metadata, tags/releases and pending notes before choosing the version. Do not overwrite unrelated work.

```powershell
git status --short --branch
git fetch origin --prune
git switch main
git pull --ff-only
git switch -c release/0.3.0
uv --version
uv version --short
uv version 0.3.0
git diff -- pyproject.toml uv.lock
```

Replace 0.3.0 with the intended version. `pyproject.toml` is authoritative; shared build identity supplies CLI, Qt, packaging and output metadata. Do not add independent runtime version strings.

**uv recipe:** `uv version <version>` updates project metadata and, by default, locks and syncs the environment. Existing locked dependency versions are preferred; a version-only bump should change only DTFlow's own version in `pyproject.toml` and `uv.lock`. Review the diff. Do not hand-edit the lockfile or pass upgrade flags. `--no-sync` still locks but skips environment synchronization; `--frozen` skips locking and is not the normal release recipe. See [uv's version guide](https://docs.astral.sh/uv/guides/package/#updating-your-version) and [lock/sync documentation](https://docs.astral.sh/uv/concepts/projects/sync/).

Move pending entries from `devdocs/releases/unreleased.md` into `devdocs/releases/v<version>.md`, then reset unreleased. Keep current highlights and limitations distinct from historical implementation checkpoints. Follow the 6.0.0 section layout in `unreleased.md` (Highlights, Added, Changed, Fixed, Upgrade and compatibility, Validations checklist, Release audit, then the workflow's CI build). Include upgrade/backup/downgrade expectations; the published GitHub release body is the source of truth for the final notes.

Check the latest PR number before preparing the release-audit bullets; verify the actual number after PR creation. Update relevant status/roadmap documents without putting a release diary in README. Create the PR early after an initial coherent commit.

## Validate and squash merge

```powershell
uv sync --locked
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run dtflow --version
uv run dtflow --help
```

`uv lock --check` verifies metadata/lock consistency; `uv sync --locked` refuses a required lockfile change. Do not use `--frozen` to hide stale metadata. Ordinary pytest is offline; see [testing](testing.md). Never add live generation/upload just to validate a version bump.

Record actual results and check documentation links. Source version output establishes the application version, not packaged commit identity. Mark the PR ready and wait for passing CI on its final head. Squash merge, then switch to main, pull with `--ff-only` and verify a clean worktree.

## Owner requests the build and smoke-tests Windows

**The owner builds locally or requests the manual Actions build, then smoke-tests the exact executable. The agent prepares/checks/merges the release PR and provides the command/workflow and expected identity. Wait for owner acceptance before publication. The local-build path tags after acceptance; the CI draft path freezes a candidate tag first, without publishing.**

Use the [build guide](building.md), including its pinned PyInstaller recipe and focused acceptance checklist. Build from the merged release commit, not the PR head or a dirty checkout. Identify the exact artifact:

```text
dist/dtflow-<version>-g<short-sha>-windows-x64.exe
```

Match filename, Qt/version display, full bundled commit and clean source status. Record SHA256, size and owner smoke results. Reuse applicable existing live evidence without claiming it was rerun or repeating credit-consuming workloads merely because the version changed. A dirty development smoke is separate from acceptance of the final clean artifact.

## Optional CI draft path

Instead of building locally, use **Build Windows EXE and draft release**; see [building](building.md) for downloads, caches and toolchain.

1. Merge the version/release PR and check CI, notes, clean main and source version. For an exploratory build, dispatch from the intended workflow ref with an empty tag: no release is touched. To inspect unmerged PR code, use the **pr** input with an empty tag; this resolves its exact same-repository head SHA and skips every release action. PR and release_tag are mutually exclusive.
2. For a release candidate, create `v<version>` on the approved merged commit with the manual **Create release tag** workflow (inputs: `version` as X.Y.Z, optional `ref`, default main). It refuses unless the commit is part of main, `pyproject.toml` has that version, `devdocs/releases/v<version>.md` exists, and neither the tag nor a release for it exists; it never moves or replaces a tag and creates no release or draft. Pushing the tag with local git is the alternative. Then dispatch **Build Windows EXE and draft release** from main with that existing **release_tag**. Code comes from the exact tag, workflow YAML from the selected dispatch ref; both identities are reported. The tag must be part of main and match the application version/notes.
3. The workflow builds once with an explicitly selected FFmpeg provider (`imageio-mini` workflow default (legacy FFmpeg + minimal LGPL probe), or explicitly select `gyan` (local CLI builder default), `tas` shared-DLL, `unpins` multicall, or `imageio` (legacy FFmpeg + Gyan probe)), verifies the provider's bundled executables, DLLs and license inventory, and stages the EXE, checksum file, manifest and `FFmpeg-THIRD-PARTY.txt` as four draft assets. It refuses published releases and existing asset names, never replaces assets and never publishes. Existing draft prose is retained; review it and add bounded owner acceptance evidence before publication. **Before publishing, confirm complete corresponding-source compliance for the chosen FFmpeg build: Gyan uses GPLv3, TAS GPL-2.0-or-later, and Unpins GPL-3.0-or-later; imageio combines an FFmpeg 4.2.2 wheel executable with GPLv3 Gyan ffprobe. Neither Unpins nor either mixed imageio binary combination has complete corresponding-source delivery independently verified by DTFlow; provenance links alone do not establish compliance.**
4. Download and verify the exact draft asset; the owner smoke-tests it on target Windows. After approval, explicitly publish that draft (GitHub UI or release command) and verify tag, notes and assets. No rebuild is needed after smoke unless source/toolchain changes.

A tag and draft are not a shipped release. Do not move an existing published tag. If a candidate fails, review/fix through a PR and choose a new candidate identity rather than silently replacing tested files. GitHub upload failures can leave a partial draft; inspect it manually before retrying.

## Publish only after artifact acceptance

For a local build, tag the verified merged commit as `v<version>`; publish a GitHub release using the versioned notes and the exact accepted executable. Include artifact identity/checksum in the release description or an attached checksum file. Verify tag target, rendered notes, release visibility and downloadable assets.

If code changes after building, merge the fix and ask the owner to rebuild before publishing. Do not silently move a published tag or reuse an old executable. Release completion requires the accepted asset, not just a successful PR.
