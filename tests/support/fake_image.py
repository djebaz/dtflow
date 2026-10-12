"""Test-only image-source substitution for fake provider generation."""

from contextlib import contextmanager
from pathlib import Path


@contextmanager
def use_fake_image(path: Path):
    """Temporarily substitute the fake source; production defaults stay untouched."""
    from dtflow.backends import fake

    previous = fake.FIXTURE_IMAGE
    fake.FIXTURE_IMAGE = path
    try:
        yield
    finally:
        fake.FIXTURE_IMAGE = previous
