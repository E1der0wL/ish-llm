"""Viewport-bounded popup drawing and local keyboard/mouse interaction."""
from prompt_toolkit.key_binding import KeyBindings, DynamicKeyBindings, merge_key_bindings
from prompt_toolkit.filters import Condition
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.containers import Container
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.screen import WritePosition
from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from ..popups.models import PopupAction
from ..core.theme import Theme, ThemeRole
from .overlay import DockRowWindow
from .interactive import InteractivePopup, PopupChrome, fitted
from .text import clusters, display_text, fit, fit_labels, width
from ..locales import Locale, Message as M


class PopupControl(FormattedTextControl):
    def __init__(self, surface, *, focusable):
        self.surface = surface
        super().__init__(lambda: surface.fragments, focusable=focusable, show_cursor=False,
                         key_bindings=surface.key_bindings if focusable else None)

    def mouse_handler(self, event):
        if event.event_type == MouseEventType.SCROLL_UP:
            self.surface.move_scroll(-3, position=event.position)
        elif event.event_type == MouseEventType.SCROLL_DOWN:
            self.surface.move_scroll(3, position=event.position)
        else:
            super().mouse_handler(event)
        return None


class PopupSurface:
    def __init__(self, invalidate, dismiss, *, modal):
        self._invalidate, self._dismiss = invalidate, dismiss
        self.fragments = []
        self.scroll = self.selected = 0
        self._max_scroll = 0
        self._page_rows = 1
        self._body_origin = (0, 0)
        self._painted_rows = []
        self._actions = ()
        self._wrapped_key = None
        self._wrapped_rows = []
        self.page = None
        self.bindings = KeyBindings()
        self._shortcuts = ()
        self._shortcut_bindings = KeyBindings()
        self.key_bindings = merge_key_bindings([
            self.bindings, DynamicKeyBindings(lambda: self._shortcut_bindings)])
        self._install_keys()
        self.control = PopupControl(self, focusable=modal)
        self.container = HSplit([DockRowWindow(self.control, always_hide_cursor=True)], modal=modal)

    def _install_keys(self):
        # Consume editing/abort keys locally; mouse and terminal protocol keys
        # retain their prompt-toolkit handlers.
        for key in (Keys.Any, "c-c", "c-d", "c-z", "c-l", Keys.BracketedPaste):
            self.bindings.add(key)(lambda event: None)
        # Escape is local back navigation. At the root it stays in the panel.
        # Unknown Alt chords must not insert their suffix into the shell.
        self.bindings.add("escape")(lambda event: None)
        self.bindings.add("escape", Keys.Any)(lambda event: None)
        # A bare Escape immediately followed by an Alt chord can reach the key
        # processor as Escape, Escape, suffix. Consume the first as Back, then
        # let the normal bindings interpret the second with its queued suffix.
        # Otherwise the unknown-Alt guard swallows both Escape keys.
        self.bindings.add("escape", "escape", eager=True)(self._consecutive_escape)
        for key, amount in (("up", -1), ("down", 1)):
            self.bindings.add(key)(lambda event, amount=amount: self.move_scroll(amount))
        self.bindings.add("pageup")(lambda event: self.move_scroll(-self._page_rows))
        self.bindings.add("pagedown")(lambda event: self.move_scroll(self._page_rows))
        self.bindings.add("home")(lambda event: self.move_scroll(-self._max_scroll))
        self.bindings.add("end")(lambda event: self.move_scroll(self._max_scroll))
        for key, amount in (("tab", 1), ("right", 1), ("s-tab", -1), ("left", -1)):
            self.bindings.add(key)(lambda event, amount=amount: self.select(amount))
        self.bindings.add("enter")(lambda event: self.activate())
        self.bindings.add("c-j")(lambda event: self.activate())

        # Specific modal pages replace these handlers without changing host focus.
        keys = (Keys.Any, Keys.BracketedPaste, "escape", "up", "down", "left", "right",
                "pageup", "pagedown", "home", "end", "tab", "s-tab", "enter", "c-j",
                "backspace", "delete", "f2", "f5", "c-a", "c-e", "c-u", "c-k", "c-z", "c-y")
        for key in keys:
            def handle(event, key=key):
                name = key.value if isinstance(key, Keys) else key
                name = "enter" if name in ("c-m", "c-j") else name
                self.page.key(name, event.data)
                self._invalidate()
            self.bindings.add(key, filter=Condition(lambda: isinstance(self.page, InteractivePopup)))(handle)

    def _consecutive_escape(self, event):
        if isinstance(self.page, InteractivePopup):
            self.page.key("escape", "")
        event.app.key_processor.feed(event.key_sequence[-1], first=True)
        self._invalidate()

    def set_shortcuts(self, entries):
        """Install host-owned modal shortcuts without rebuilding on every draw."""
        entries = tuple(entries)
        if entries == self._shortcuts:
            return
        bindings = KeyBindings()
        for entry in entries:
            bindings.add(*entry.sequence.keys, filter=Condition(entry.enabled), eager=True)(entry.handler)
        self._shortcuts, self._shortcut_bindings = entries, bindings

    def reset(self):
        self.scroll = self.selected = 0
        self._wrapped_key = None
        self._wrapped_rows = []

    @property
    def painted_height(self):
        return len(self._painted_rows)

    def move_scroll(self, amount, *, position=None):
        if isinstance(self.page, InteractivePopup):
            if position is not None and 0 <= position.y < len(self._painted_rows):
                # Window maps mouse x to a UIContent character index. Page
                # regions use terminal cells, including Korean/wide glyphs.
                text = "".join(item[1] for item in self._painted_rows[position.y])
                x = width(text[:position.x])
                if self.page.scroll_at(x - self._body_origin[0], position.y - self._body_origin[1], amount):
                    self._invalidate()
                    return
            self.page.key("scroll-up" if amount < 0 else "scroll-down", "")
            self._invalidate()
            return
        self.scroll = min(self._max_scroll, max(0, self.scroll + amount))
        self._invalidate()

    def select(self, amount):
        if self._actions:
            self.selected = (self.selected + amount) % len(self._actions)
            self._invalidate()

    def activate(self, index=None):
        if self._actions:
            self._actions[self.selected if index is None else index].invoke()
            self.scroll = 0
            self._invalidate()

    def _click(self, callback):
        def handle(event):
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                callback()
            return None
        return handle

    @staticmethod
    def _wrapped(content, cells, style, theme):
        result = []
        for row in content.rows_at(cells, theme):
            current, used = [], 0
            for span in row.spans:
                paint = style.paint(theme=theme, color=span.color or None, bold=span.bold)
                for cluster in clusters(display_text(span.text)):
                    size = width(cluster)
                    if used + size > cells and current:
                        result.append(current)
                        current, used = [], 0
                    if size <= cells:
                        current.append((paint, cluster))
                        used += size
            result.append(current)
        return result

    def draw(self, content, panel, rect, *, notification=False, theme: Theme = Theme(), locale=Locale(), status_hint=None):
        w, h = rect.width, rect.height
        if not w or not h:
            self.fragments = []
            self._painted_rows = []
            return
        interactive = isinstance(self.page, InteractivePopup)
        body_only = interactive and self.page.chrome == PopupChrome.BODY_ONLY
        border = int(panel.border and w >= panel.COMPACT_WIDTH and h >= (3 if body_only else panel.COMPACT_HEIGHT + 2))
        inner_w, inner_h = w - border * 2, h - border * 2
        left, top, right, bottom = panel.padding.fit(inner_w, inner_h, keep_height=panel.COMPACT_HEIGHT)
        body_w, body_h = inner_w - left - right, inner_h - top - bottom
        # A small terminal can still display content and accept Escape.
        title_rows = int(not body_only and body_h >= 2)
        status_rows = int(not body_only and body_h >= 3 and (not notification or bool(status_hint)))
        title_padding = int(not body_only and not notification and body_h >= 5)
        self._body_origin = border + left, border + title_rows + top + title_padding
        footer_rows = int(not notification and content.footer and not interactive and body_h >= 6)
        self._page_rows = max(1, body_h - title_rows - title_padding - footer_rows - status_rows)
        if not interactive:
            key = (content, body_w, panel.style, theme)
            if key != self._wrapped_key:
                self._wrapped_key = key
                self._wrapped_rows = self._wrapped(content, body_w, panel.style, theme)
        wrapped = (self.page.draw_body(body_w, self._page_rows, theme) if interactive else self._wrapped_rows)
        if notification and not interactive:
            # Notifications fit their content; a spare fixed-height body row
            # would otherwise appear as a second bottom padding row.
            self._page_rows = min(self._page_rows, max(1, len(wrapped)))
        self._max_scroll = 0 if interactive else max(0, len(wrapped) - self._page_rows)
        self.scroll = min(self.scroll, self._max_scroll)
        normal, edge = panel.style.paint(theme=theme), panel.style.paint(theme=theme, border=True)
        rows = []
        self._actions = (() if notification else
                         (*content.actions, PopupAction(locale.text(M.CLOSE), self._dismiss)))
        self.selected = self.selected % len(self._actions) if self._actions else 0

        def full(parts):
            used = sum(width(part[1]) for part in parts)
            return [(normal, " " * left), *parts, (normal, " " * max(0, body_w - used + right))]

        if title_rows:
            close = "  " if inner_w >= 4 else ""
            title = fit(" " + content.title, max(0, inner_w - width(close)))
            title_style = theme.paint(ThemeRole.TITLE, background=ThemeRole.BUTTON_BG, bold=True)
            close_style = theme.paint(background=ThemeRole.BUTTON_BG)
            rows.append([(title_style, title),
                         (title_style, " " * max(0, inner_w - width(title) - width(close))),
                         (close_style, close, self._click(self._dismiss))])
        for _ in range(top + title_padding):
            rows.append([(normal, " " * inner_w)])
        for row in wrapped[self.scroll:self.scroll + self._page_rows]:
            rows.append(full(row))
        while len(rows) < top + title_rows + title_padding + self._page_rows:
            rows.append([(normal, " " * inner_w)])
        if footer_rows:
            labels = fit_labels([f" {a.label} " for a in self._actions], body_w, " ")
            # If the whole action row cannot be read, keep the selected action
            # visible and let Tab/arrow keys cycle through the remaining ones.
            if any(width(label) < 3 for label in labels):
                labels = [fit(f" {self._actions[self.selected].label} ", body_w)]
                indices = [self.selected]
            else:
                indices = list(range(len(self._actions)))
            parts = []
            for index, label in zip(indices, labels):
                if parts:
                    parts.append((normal, " "))
                selected = not notification and index == self.selected
                paint = theme.button(selected=selected)
                parts.append((paint, label,
                              self._click(lambda index=index: self.activate(index))))
            parts.insert(0, (normal, " " * max(0, (body_w - sum(width(part[1]) for part in parts)) // 2)))
            rows.append(full(parts))
        for _ in range(bottom):
            rows.append([(normal, " " * inner_w)])
        if status_rows:
            hint = status_hint if status_hint is not None else (self.page.status_text() if interactive else
                    locale.text(M.POPUP_HINT))
            status = theme.paint(ThemeRole.MUTED, background=ThemeRole.BUTTON_BG)
            label = fit(" " + hint, inner_w, marker="…")
            rows.append([(status, label + " " * max(0, inner_w - width(label)))])
        if border:
            rows = [[(edge, "┌" + "─" * (w - 2) + "┐")],
                    *[[(edge, "│"), *row, (edge, "│")] for row in rows],
                    [(edge, "└" + "─" * (w - 2) + "┘")]]
        self.fragments = []
        self._painted_rows = rows[:h]
        for index, row in enumerate(self._painted_rows):
            if index:
                self.fragments.append((normal, "\n"))
            self.fragments.extend(row)


class PopupLayer(Container):
    def __init__(self, host):
        self.host = host
        self.general = PopupSurface(host.invalidate, host.dismiss, modal=True)
        self.notification = PopupSurface(host.invalidate, host.dismiss_notification, modal=False)
        self.mouse_regions = []
        self.notification_painted = False

    def reset(self):
        for child in self.get_children():
            child.reset()

    def preferred_width(self, max_available_width):
        return Dimension.exact(0)

    def preferred_height(self, width, max_available_height):
        return Dimension.exact(0)

    def get_children(self):
        return [self.general.container, self.notification.container]

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        self.mouse_regions = []
        self.notification_painted = False
        if self.host.modal:
            surface, panel, content = self.general, self.host.panel, self.host.page.content()
            notification = False
        elif self.host.notification_visible:
            surface = self.notification
            panel = self.host.config.notifications.panel
            content = self.host.notifications.current.content
            notification = True
        else:
            return
        anchor = self.host.anchor(screen, write_position) if not notification else None
        rect = (panel.fit_cursor(write_position.width, write_position.height, *anchor) if anchor is not None else
                panel.fit(write_position.width, write_position.height, notification=notification))
        if not rect.width or not rect.height:
            return
        surface.draw(content, panel, rect, notification=notification, theme=self.host.theme, locale=self.host.locale,
                     status_hint=self.host.notification_hint() if notification else self.host.status_text())
        height = surface.painted_height if notification else rect.height
        top = rect.top + (rect.height - height if notification else 0)
        position = WritePosition(write_position.xpos + rect.left, write_position.ypos + top, rect.width, height)
        surface.container.write_to_screen(screen, mouse_handlers, position, parent_style, True, z_index)
        self.mouse_regions = [(position.xpos, position.xpos + rect.width, y)
                              for y in range(position.ypos, position.ypos + height)]
        self.notification_painted = notification
