"""Extensible, display-only assistant output blocks."""

from .model import OutputBlock, OutputObject, RenderContext
from .parser import OutputParser
from .registry import RendererRegistry, OutputRenderer
from .images import ImageRenderer

__all__ = ["OutputBlock", "OutputObject", "RenderContext", "OutputParser", "RendererRegistry", "OutputRenderer", "ImageRenderer"]
