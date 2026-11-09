"""Own a reversible style layer on PromptSession's public style property."""
from prompt_toolkit.styles import DynamicStyle, Style, merge_styles

from ...features.completion.config import CompletionMenu
from ...core.theme import Theme


class CompletionAppearance:
    def __init__(self, prompt):
        self._prompt = prompt
        self._installed = self._previous = self._style = None
        self._active = False
        self._key = None

    def configure(self, config: CompletionMenu, theme: Theme, *, enabled: bool):
        active = enabled and config.enabled
        if not active:
            self.close()
            return
        key = (config, theme)
        if key != self._key:
            self._style = Style.from_dict(config.rules(theme, scoped_scrollbar=True))
            self._key = key
        self._active = True
        if self._installed is None:
            previous = self._previous = self._prompt.style
            # Capture the previous style so another plugin can safely keep this
            # layer after dok unloads. Its base style remains intact.
            self._installed = merge_styles([
                DynamicStyle(lambda: previous),
                DynamicStyle(lambda: self._style if self._active else None),
            ])
            self._prompt.style = self._installed

    def close(self):
        self._active = False
        if self._installed is not None and self._prompt.style is self._installed:
            self._prompt.style = self._previous
        self._installed = self._previous = None
