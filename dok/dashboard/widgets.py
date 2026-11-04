"""Widget contract and the ordered set of currently implemented body widgets."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

from ..core.elements import ElementConfig
from ..features.audio.view import VisualizerView
from ..features.media.view import ProgressView, TitleView, TransportView
from ..features.shortcuts.view import ShortcutView
from ..rendering.presentation import RenderContext


class DockRow(IntEnum):
    VISUALIZER = 0
    PROGRESS = 1
    TITLE = 2
    TRANSPORT = 3
    SHORTCUTS = 4


class WidgetView(Protocol):
    @property
    def element(self) -> ElementConfig: ...

    def render(self, context: RenderContext) -> tuple: ...


@dataclass(frozen=True, slots=True)
class BodyWidget:
    row: DockRow
    view: WidgetView

    def rows(self, mode):
        return self.view.element.arrange((self.row,), mode, None)


def body_widgets(config, execute=None) -> tuple[BodyWidget, ...]:
    """The single declaration of body order and feature-to-setting bindings."""
    return (
        BodyWidget(DockRow.VISUALIZER, VisualizerView(config.widgets.visualizer)),
        BodyWidget(DockRow.PROGRESS, ProgressView(config.widgets.progress)),
        BodyWidget(DockRow.TITLE, TitleView(config.widgets.title)),
        BodyWidget(DockRow.TRANSPORT, TransportView(config.widgets.media_controls)),
        BodyWidget(DockRow.SHORTCUTS, ShortcutView(config, execute)),
    )
