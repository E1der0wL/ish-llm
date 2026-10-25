"""Two-panel pages with one reserved progress/notice row and shared focus rules."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from ...widget.panel_edge import PanelEdge
from ...widget.focus_region import FocusRegion
from ...config.theme_style import ThemeSelector


class TwoPanelPage:
    ABSENT = None
    SIDEBAR_VISIBLE = True
    EMPTY_ANNOTATION = ""
    STATUS_PADDING = 1
    STATUS_HEIGHT = 1
    HEADER_HEIGHT = 1
    FOOTER_HEIGHT = 1
    BODY_MIN_HEIGHT = 3
    BODY_WEIGHT = 1
    FORWARD = False
    PREVIOUS = -1
    NEXT = 1

    def __init__(
        self,
        *,
        header,
        footer,
        sidebar,
        main,
        progress,
        notice,
        sidebar_controls,
        focus_sidebar,
        focus_main,
        sidebar_visible=lambda: TwoPanelPage.SIDEBAR_VISIBLE,
        sidebar_width=ABSENT,
        resize_sidebar=ABSENT,
        categories=(),
        status_annotation=lambda: TwoPanelPage.EMPTY_ANNOTATION
    ):
        self.sidebar_controls = sidebar_controls
        self.categories = categories
        self._focus_sidebar, self._focus_main = focus_sidebar, focus_main
        visible = Condition(sidebar_visible)
        self.status = progress.container(notice, annotation=status_annotation)
        self.edge = (
            PanelEdge(sidebar_width, resize_sidebar)
            if resize_sidebar
            else Window(
                width=PanelEdge.WIDTH,
                char=PanelEdge.GLYPH,
                style=ThemeSelector.DIVIDER.style,
            )
        )
        self.sidebar_region = FocusRegion(sidebar, active=lambda: self.sidebar_focused)
        self.main_region = FocusRegion(
            HSplit(
                [
                    main,
                    VSplit(
                        [
                            Window(width=TwoPanelPage.STATUS_PADDING),
                            self.status,
                            Window(width=TwoPanelPage.STATUS_PADDING),
                        ],
                        height=TwoPanelPage.STATUS_HEIGHT,
                    ),
                ]
            ),
            active=lambda: not self.sidebar_focused,
        )
        self.container = HSplit(
            [
                HSplit([header], height=TwoPanelPage.HEADER_HEIGHT),
                VSplit(
                    [
                        ConditionalContainer(self.sidebar_region, visible),
                        ConditionalContainer(self.edge, visible),
                        self.main_region,
                    ],
                    height=Dimension(
                        min=TwoPanelPage.BODY_MIN_HEIGHT,
                        weight=TwoPanelPage.BODY_WEIGHT,
                    ),
                ),
                HSplit([footer], height=TwoPanelPage.FOOTER_HEIGHT),
            ],
            style=ThemeSelector.ROOT.style,
        )

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

    def cycle_category(self, reverse=FORWARD):
        current = get_app().layout.current_control
        if current in self.categories:
            index = (
                self.categories.index(current)
                + (TwoPanelPage.PREVIOUS if reverse else TwoPanelPage.NEXT)
            ) % len(self.categories)
            self.categories[index].activate()
            get_app().invalidate()

    def __pt_container__(self):
        return self.container
