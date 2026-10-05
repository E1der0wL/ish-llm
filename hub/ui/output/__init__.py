"""Extensible, display-only assistant output blocks."""

from .model import OutputBlock, RenderContext
from .parser import OutputParser
from .registry import RendererRegistry, OutputRenderer
from .images import ImageRenderer

__all__ = ["OutputBlock", "RenderContext", "OutputParser", "RendererRegistry", "OutputRenderer", "ImageRenderer"]
