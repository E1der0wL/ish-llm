"""Shared Home output geometry, tag navigation and engine-owned interactions."""

from abc import ABC, abstractmethod
from pathlib import Path

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.containers import AnyContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension

from ..content_inset import ContentInset
from .tag_bar import OutputTag, TagBar
from ...config.theme import HubTheme
from ...config.view_state import ReadingPosition
from ...model import ChatMessage
from ...ui.input.registry import ShortcutRegistry
from ...config.theme_style import ThemeSelector


class EngineOutput(HSplit, ABC):
    """Every engine supplies its content and tags inside the same output frame."""

    ABSENT = None
    OPEN = False
    CLOSED = True
    MIN_WIDTH = 1
    RIGHT_INSET = 1
    HEADER_HEIGHT = 1
    MIN_HEIGHT = 3
    FLEX_WEIGHT = 1
    TAGS = "tags"
    DETAILS = "details"
    FIND = "find"

    def __init__(self, view, content: AnyContainer, *, heading, shortcuts=ABSENT):
        self.view = view
        self._closed = EngineOutput.OPEN
        self.available_width = max(EngineOutput.MIN_WIDTH, view._columns() - EngineOutput.RIGHT_INSET)
        self.shortcuts = shortcuts or ShortcutRegistry()
        self.inspector = EngineOutput.ABSENT
        self.tags = TagBar(self, view)
        from .participants import ParticipantBar
        self.participants = ParticipantBar(self, view)
        self.header = Window(
            FormattedTextControl(heading),
            height=EngineOutput.HEADER_HEIGHT,
            style=ThemeSelector.TITLE.style,
        )
        self.body = VSplit([content, self.tags.container, self.participants.container])
        super().__init__(
            [ContentInset(HSplit([self.header, self.body]))],
            height=Dimension(min=EngineOutput.MIN_HEIGHT, weight=EngineOutput.FLEX_WEIGHT),
        )

    def __pt_container__(self):
        return self

    def write_to_screen(
        self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
    ):
        self.available_width = max(
            EngineOutput.MIN_WIDTH, write_position.width - EngineOutput.RIGHT_INSET
        )
        super().write_to_screen(
            screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
        )

    @property
    def modal_container(self):
        if self.participants.visible:
            return self.participants.container
        return self.tags.container if self.tags.visible else EngineOutput.ABSENT

    def show_sidebar(self, sidebar):
        for item in (self.tags, self.participants):
            item.visible = item is sidebar

    @property
    def floating_dialog(self) -> AnyContainer | None:
        return EngineOutput.ABSENT

    @abstractmethod
    def tag_entries(self) -> tuple[OutputTag, ...]: ...

    def tag_position(self) -> int:
        return self.reading_position().line

    @abstractmethod
    def select_tag(self, tag: OutputTag) -> None: ...

    def expand_tag(self, tag: OutputTag) -> None:
        """Optional interaction supplied by the engine's output implementation."""

    def position_changed(self):
        if self.tags.visible:
            self.tags.track_position()
        if self.view.on_output_position_changed:
            self.view.on_output_position_changed()

    @abstractmethod
    def set_messages(
        self,
        messages: tuple[ChatMessage, ...],
        position: ReadingPosition | None = ABSENT,
    ) -> None: ...

    @abstractmethod
    def update_messages(
        self, messages: tuple[ChatMessage, ...], *, auto_scroll: bool | None = ABSENT
    ) -> None: ...

    @abstractmethod
    def reading_position(self) -> ReadingPosition: ...

    @abstractmethod
    def restore_position(self, position: ReadingPosition | None) -> None: ...

    @abstractmethod
    def set_follow_tail(self, enabled: bool) -> None: ...

    @abstractmethod
    def set_theme(self, theme: HubTheme) -> None: ...

    @abstractmethod
    def set_file_root(self, path: Path) -> None: ...

    @abstractmethod
    def scroll(self, key: str) -> None: ...

    def find(self) -> None:
        """Optional engine-specific search UI."""

    def clear_search(self) -> None:
        """Release any engine-specific search highlights."""

    def invoke(self, action: str) -> None:
        if action == EngineOutput.TAGS:
            self.tags.open()
        elif action == EngineOutput.DETAILS:
            self.tags.close() if self.tags.visible else self.tags.open()
        elif action == EngineOutput.FIND:
            self.find()

    def deactivate(self) -> None:
        app = get_app()
        focused_tag = app.layout.current_control in (
            self.tags.control,
            self.tags.collapse.control,
            self.participants.control,
            self.participants.collapse.control,
            self.participants.add_button.control,
        )
        if self.view._dialog is not EngineOutput.ABSENT and self.view._dialog in (
            self.floating_dialog,
            self.inspector,
        ):
            self.view.dialogs.close(restore_focus=app.is_running)
        self.tags.visible = TagBar.HIDDEN
        self.participants.visible = TagBar.HIDDEN
        self.clear_search()
        self.inspector = EngineOutput.ABSENT
        if app.is_running and focused_tag:
            self.view._focus_composer()

    def close(self) -> None:
        if not self._closed:
            self._closed = EngineOutput.CLOSED
            self.deactivate()
            self.release()

    @abstractmethod
    def release(self) -> None:
        """Release owned resources; Home's shared render worker stays alive."""
