"""Imports every family module once so each registers itself (explicit list, no directory scan).

The import order is the display order of ``supported_families()``.
"""

from dtflow.services.lora_convert.families import krea2, zimage, flux1, sd1, sdxl  # isort: skip

__all__ = ["krea2", "zimage", "flux1", "sd1", "sdxl"]
