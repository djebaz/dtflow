"""Execution-store interface used by application services."""

from pathlib import Path
from typing import Protocol

from dtflow.core.models import BackendFailure, RemoteOutcome, ResolvedGenerationRequest, SubmissionStage


class AttemptStore(Protocol):
    async def create(self, attempt_id: str, backend_id: str, request: ResolvedGenerationRequest) -> None: ...
    async def transition(
        self,
        attempt_id: str,
        expected: tuple[str, ...],
        state: str,
        stage: SubmissionStage,
        outcome: RemoteOutcome,
        *,
        failure: BackendFailure | None = None,
        artifacts: tuple[str, ...] = (),
    ) -> None: ...
    async def inspect(self, attempt_id: str | None = None) -> tuple[dict, ...]: ...
    async def backup(self, target: Path) -> None: ...
