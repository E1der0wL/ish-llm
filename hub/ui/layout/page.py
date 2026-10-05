"""Two-panel pages with one reserved progress/notice row and shared focus rules."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension


class TwoPanelPage:
    def __init__(self, *, header, footer, sidebar, main, progress, notice,
                 sidebar_controls, focus_sidebar, focus_main, sidebar_visible=lambda: True,
                 extra=()):
        self.sidebar_controls = sidebar_controls
        self._focus_sidebar, self._focus_main = focus_sidebar, focus_main
        visible = Condition(sidebar_visible)
        self.status = progress.container(notice)
        self.container = HSplit([
            HSplit([header], height=1),
            VSplit([
                ConditionalContainer(sidebar, visible),
                ConditionalContainer(Window(width=1, char="│", style="class:hub.divider"), visible),
                HSplit([main, VSplit([Window(width=1), self.status, Window(width=1)], height=1)]), *extra,
            ], height=Dimension(min=3, weight=1)),
            HSplit([footer], height=1),
        ], style="class:hub")

    @property
    def sidebar_focused(self):
        return get_app().layout.current_control in self.sidebar_controls()

    def focus_sidebar(self):
        if not self.sidebar_focused:
            self._focus_sidebar()
            get_app().invalidate()

    def focus_main(self):
        self._focus_main()
        get_app().invalidate()

    def __pt_container__(self):
        return self.container
