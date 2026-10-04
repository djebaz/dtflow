"""Pixel encoding feeds the same validator that final artifacts must pass."""

import os

import pytest

from dtflow.core.png import encode_png, validate_png


@pytest.mark.parametrize("channels", [1, 3, 4])
def test_encoded_pixels_pass_final_artifact_validation(channels):
    validate_png(encode_png(os.urandom(5 * 3 * channels), 5, 3, channels), 5, 3, decoded_limit=10_000)


@pytest.mark.parametrize("channels,size", [(2, 30), (3, 44), (3, 0)])
def test_encoder_rejects_inconsistent_pixel_data(channels, size):
    with pytest.raises(ValueError):
        encode_png(bytes(size), 5, 3, channels)
