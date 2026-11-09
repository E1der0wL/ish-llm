"""Public host completion, literal parsing and one explicitly submitted lookup."""
from dataclasses import dataclass

from prompt_toolkit.completion import Completer, ThreadedCompleter
from prompt_toolkit.completion import Completion as Suggestion

from ...features.completion.models import Catalog
from ...features.completion.analysis import HelpAnalysis


@dataclass(frozen=True, slots=True)
class LookupOutput:
    command: str
    text: str
    truncated: bool = False
    error: str = ""


class RegisteredCompleter(Completer):
    def __init__(self, host, previous):
        self.host, self.previous = host, previous
        self.active = True

    @staticmethod
    def insertion(document, item):
        # Completion providers may include or omit the argument-ending space.
        # Compare the resulting prefix, not replacement offsets or display labels.
        end = len(document.text_before_cursor) + item.start_position
        return (document.text_before_cursor[:end] + item.text).rstrip(" \t")

    def get_completions(self, document, complete_event):
        host = self.host
        seen = set()
        if self.previous is not None:
            for item in self.previous.get_completions(document, complete_event):
                seen.add(self.insertion(document, item))
                yield item
        if self.active and host.enabled:
            word = host.prompt.completion_context(document)
            if word is not None and not word.dynamic and not word.command:
                words = host.parse(document.text[:word.start])
                if words:
                    for text, description, open_value in host.catalog.suggestions(words, word.value):
                        insert = host.prompt.quote_argument(text) + ("" if open_value else " ")
                        offset = word.start - document.cursor_position
                        item = Suggestion(insert, start_position=offset, display=text, display_meta=description)
                        key = self.insertion(document, item)
                        if key not in seen:
                            seen.add(key)
                            yield item


class CompletionHost:
    """One reference owns the installed provider; never remove another plugin's provider."""
    def __init__(self, prompt, submission):
        self.prompt, self.submission = prompt, submission
        self.catalog = Catalog()
        self.enabled = False
        self._provider = self._installed = self._previous = None
        self._pending = None

    def parse(self, text):
        return self.prompt.interactive_shell.adapter.parsing.tool_argv(text)

    def prefix(self, text):
        words = self.parse(text)
        if not words:
            raise ValueError("Prefix must contain literal command words, without shell expressions")
        return tuple(words)

    def label(self, words):
        return " ".join(self.prompt.quote_argument(word) for word in words)

    def suggest_prefix(self, command):
        words = self.parse(command) or ()
        prefix = []
        for word in words:
            if word.startswith("-"):
                break
            prefix.append(word)
        return self.label(prefix)

    def configure(self, enabled):
        self.enabled = enabled
        if enabled and self._installed is None:
            self._previous = self.prompt.completer
            self._provider = RegisteredCompleter(self, self._previous)
            # PromptSession's public completer property allows native-first order.
            # ish.set_completer appends native completion again, so it must not be
            # used here: query the entire existing chain exactly once.
            self._installed = ThreadedCompleter(self._provider)
            self.prompt.completer = self._installed
        elif not enabled and self._installed is not None:
            self._provider.active = False
            if self.prompt.completer is self._installed:
                self.prompt.completer = self._previous
            self._installed = self._previous = self._provider = None

    def validate(self, command):
        if not command.strip() or "\n" in command or "\r" in command:
            raise ValueError("Use a single-line lookup command so its output can be identified")
        words = self.parse(command)
        if words and words[0] in {*self.prompt.internal_commands, *self.prompt.internal_tools}:
            raise ValueError("Lookup requires a shell command, not an ish internal tool")

    def submit(self, command):
        if self._pending is not None:
            raise ValueError("A lookup command is already pending")
        self.validate(command)
        shell = self.prompt.interactive_shell
        self._pending = (self.prompt.app.future, shell.accepted_prompt_id, command)
        if not self.submission.execute_lookup(command):
            self._pending = None
            raise ValueError("ish did not accept the lookup command")

    def observe(self):
        """Read only when ish enters the next prompt; no polling and no hook replacement."""
        if self._pending is None or self._pending[0] is self.prompt.app.future:
            return None
        _, identity, command = self._pending
        self._pending = None
        shell = self.prompt.interactive_shell
        if shell.accepted_prompt_id == identity or shell.last_command != command:
            return LookupOutput(command, "", error="The lookup output could not be attributed to this command")
        return LookupOutput(command, HelpAnalysis.clean(shell.scrollback.last_output), shell.scrollback.last_output_truncated)

    def close(self):
        self.configure(False)
        self._pending = None
