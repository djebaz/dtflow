"""Session-only gallery selection state, independent from focused-image navigation."""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_CHECKED_IMAGES = 10_000


@dataclass(slots=True)
class GallerySelection:
    """Track stable output identities without keeping image objects in memory."""

    checked: set[str] = field(default_factory=set)
    anchor: str | None = None

    def toggle(self, identity: str) -> bool:
        """Toggle one identity and return its new checked state."""
        if identity in self.checked:
            self.checked.remove(identity)
            active = False
        elif len(self.checked) < MAX_CHECKED_IMAGES:
            self.checked.add(identity)
            active = True
        else:
            return False
        self.anchor = identity
        return active

    def add(self, identities: tuple[str, ...] | list[str]) -> int:
        """Add identities up to the shared operation limit, preserving earlier checks."""
        before = len(self.checked)
        for identity in identities:
            if identity not in self.checked and len(self.checked) >= MAX_CHECKED_IMAGES:
                break
            self.checked.add(identity)
        if identities:
            self.anchor = identities[-1]
        return len(self.checked) - before

    def clear(self) -> None:
        """Forget all checks but leave focused browsing untouched."""
        self.checked.clear()
        self.anchor = None

    def snapshot(self) -> tuple[str, ...]:
        """Freeze IDs for a bulk action without exposing mutable state."""
        return tuple(sorted(self.checked))
