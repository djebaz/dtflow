"""Shared tokens reach both native styles and the painted canvas."""

from dtflow.ui.theme import load_theme, load_tokens


def test_theme_resolves_all_tokens_and_readable_placeholder_palette():
    source, tokens = load_theme(), load_tokens()
    assert "${" not in source
    assert tokens["color.accent"] in source

    def luminance(hex_color):
        parts = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in parts]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear, strict=True))

    for name in ("color.text", "color.secondary", "color.placeholder"):
        for surface in ("color.background", "color.surface", "color.input", "color.canvas"):
            light, dark = sorted((luminance(tokens[name]), luminance(tokens[surface])), reverse=True)
            assert (light + 0.05) / (dark + 0.05) >= 4.5
