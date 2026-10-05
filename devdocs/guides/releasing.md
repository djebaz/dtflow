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

Move pending entries from `devdocs/releases/unreleased.md` into `devdocs/releases/v<version>.md`, then reset unreleased. Keep current highlights and limitations distinct from historical implementation checkpoints. Include upgrade/backup/downgrade expectations and distinguish agent checks from owner-run evidence.

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

## Owner builds and smoke-tests Windows

**The owner runs the Windows executable build and manual smoke test. The agent prepares/checks/merges the release PR, then tells the owner when clean main is ready, with the exact command and expected filename. Wait for their build result and acceptance before tagging or publishing.**

Use the [build guide](building.md), including its pinned PyInstaller recipe and focused acceptance checklist. Build from the merged release commit, not the PR head or a dirty checkout. Identify the exact artifact:

```text
dist/dtflow-<version>-g<short-sha>-windows-x64.exe
```

Match filename, Qt/version display, full bundled commit and clean source status. Record SHA256, size and owner smoke results. Reuse applicable existing live evidence without claiming it was rerun or repeating credit-consuming workloads merely because the version changed. A dirty development smoke is separate from acceptance of the final clean artifact.

## Publish only after artifact acceptance

Tag the verified merged commit as `v<version>`; publish a GitHub release using the versioned notes and the exact accepted executable. Include artifact identity/checksum in the release description or an attached checksum file. Verify tag target, rendered notes, release visibility and downloadable assets.

If code changes after building, merge the fix and ask the owner to rebuild before publishing. Do not silently move a published tag or reuse an old executable. Release completion requires the accepted asset, not just a successful PR.
