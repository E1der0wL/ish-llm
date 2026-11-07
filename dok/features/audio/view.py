"""Render captured levels using the visualizer owner's length and palette."""

from ...rendering.presentation import RenderContext, swallow_mouse
from .config import VisualizerConfig


class VisualizerView:
    LEVEL_GLYPHS = "▁▂▃▄▅▆▇█"

    def __init__(self, config: VisualizerConfig):
        self.element = config

    def render(self, context: RenderContext) -> tuple:
        levels = self.element.display_levels(context.audio.levels, context.width)
        text = "".join(self.LEVEL_GLYPHS[round(value * (len(self.LEVEL_GLYPHS) - 1))] for value in levels)
        return tuple((context.theme.literal(color), char, swallow_mouse)
                     for char, color in zip(text, self.element.style.palette(len(text))))
