"""Explicit shell argument-prefix grants for a deliberately small shell grammar.

This is a condition evaluator, not a shell runner or a risk classifier. Compound
commands retain their operators and every command must match its own prefix.
Anything outside the supported grammar requires another approval.
"""

from dataclasses import dataclass
import json
import shlex
from jsonschema import ValidationError

from llm.components.tools.builtin.process_values import ShellArguments
from llm.core.approval import ApprovalCondition
from llm.services.runtime.approval import ApprovalEvaluator
from .approval_matching import CommandRangeConfig, MatchedToolAction


@dataclass(frozen=True, slots=True)
class CommandChain:
    CONTROL_BOUNDARY = 32
    TAB = '\t'
    INVALID_SYNTAX = 'Unsupported shell syntax for a command-range rule'
    QUOTES = '"\''
    PUNCTUATION = '|&;'
    QUOTED_OPERATOR = 'Quoted shell operators require exact approval'
    INVALID_SEPARATOR = 'Unsupported command separator'
    INCOMPLETE = 'A complete command is required'
    ASSIGNMENT = '='
    CONTROL_FLOW = 'Shell assignments and control flow require exact approval'
    OPTION_PREFIX = '-'
    SUGGESTED_WORDS = 2
    LINE_SEPARATOR = '\n'
    COMPOUND_PREFIX = 'Write one command prefix per line'
    commands: tuple[tuple[str, ...], ...]
    operators: tuple[str, ...]

    OPERATORS = frozenset(('|', '&&', '||', ';'))
    # Reject even quoted occurrences conservatively. No expansions, redirects,
    # comments, shell control flow, background jobs or hidden nested commands.
    UNSUPPORTED = frozenset('><$`()\\{}[]*?~#!^\n\r')
    RESERVED = frozenset(('if', 'then', 'else', 'elif', 'fi', 'for', 'while',
                          'until', 'do', 'done', 'case', 'esac', 'select',
                          'function', '!', 'time', 'coproc'))

    @classmethod
    def parse(cls, command):
        if not isinstance(command, str) or any(c in cls.UNSUPPORTED or ord(c) < CommandChain.CONTROL_BOUNDARY and c != CommandChain.TAB
                                               for c in command):
            raise ValueError(CommandChain.INVALID_SYNTAX)
        # A punctuation token inside quotes must not be mistaken for an operator.
        # Only plain words and simple quotes containing no shell punctuation are
        # accepted; shlex subsequently preserves quoted whitespace as one argv.
        quote = None
        for char in command:
            if char in CommandChain.QUOTES:
                if quote == char:
                    quote = None
                elif quote is None:
                    quote = char
            elif quote and char in CommandChain.PUNCTUATION:
                raise ValueError(CommandChain.QUOTED_OPERATOR)
        lexer = shlex.shlex(command, posix=True, punctuation_chars=CommandChain.PUNCTUATION)
        lexer.whitespace_split = True
        lexer.commenters = ''
        commands, operators, current = [], [], []
        for token in lexer:
            if token and all(c in CommandChain.PUNCTUATION for c in token):
                if token not in cls.OPERATORS or not current:
                    raise ValueError(CommandChain.INVALID_SEPARATOR)
                commands.append(tuple(current))
                operators.append(token)
                current = []
            else:
                current.append(token)
        if not current:
            raise ValueError(CommandChain.INCOMPLETE)
        commands.append(tuple(current))
        if any(not args[0] or CommandChain.ASSIGNMENT in args[0] or args[0] in cls.RESERVED for args in commands):
            raise ValueError(CommandChain.CONTROL_FLOW)
        return cls(tuple(commands), tuple(operators))

    def matches(self, prefixes):
        return len(prefixes) == len(self.commands) and all(
            prefix and tuple(args[:len(prefix)]) == tuple(prefix)
            for args, prefix in zip(self.commands, prefixes))

    def suggested(self):
        # Show a concrete subcommand when present. This is only a UI suggestion;
        # it has no authority until the user explicitly accepts the displayed rule.
        return tuple(args[:CommandChain.SUGGESTED_WORDS] if len(args) > 1 and not args[1].startswith(CommandChain.OPTION_PREFIX) else args[:1]
                     for args in self.commands)

    @staticmethod
    def text(prefixes):
        return CommandChain.LINE_SEPARATOR.join(shlex.join(prefix) for prefix in prefixes)

    @classmethod
    def prefixes(cls, text):
        result = []
        for line in text.splitlines():
            chain = cls.parse(line)
            if chain.operators:
                raise ValueError(CommandChain.COMPOUND_PREFIX)
            result.append(chain.commands[0])
        return tuple(result)


class CommandRangeApproval(ApprovalEvaluator):
    MISMATCHED_PREFIX = 'Every prefix must match its original command'
    IMPLEMENTATION = 'hub-shell-command-range'
    REVISION = '1'
    COMMAND = ShellArguments.command.__name__

    def __init__(self):
        super().__init__(revision=self.REVISION)

    def describe_config(self):
        return CommandRangeConfig.schema()

    def validate_config(self, config):
        super().validate_config(config)
        value = CommandRangeConfig.from_dict(config)
        chain = CommandChain.parse(value.action.arguments[self.COMMAND])
        if not chain.matches(value.prefixes):
            raise ValueError(CommandRangeApproval.MISMATCHED_PREFIX)

    @classmethod
    def condition(cls, action, prefixes):
        config = CommandRangeConfig(MatchedToolAction.capture(action), tuple(tuple(p) for p in prefixes)).to_dict()
        cls().validate_config(config)
        return ApprovalCondition(cls.IMPLEMENTATION, cls.REVISION, config)

    @classmethod
    def config(cls, condition):
        if (condition.implementation, condition.revision) != (cls.IMPLEMENTATION, cls.REVISION):
            return None
        cls().validate_config(condition.config)
        return CommandRangeConfig.from_dict(condition.config)

    @classmethod
    def available(cls, action):
        try:
            action = MatchedToolAction.capture(action)
            if action.tool != ShellArguments.NAME:
                return False
            chain = CommandChain.parse(action.arguments.get(cls.COMMAND))
            cls.condition(action, chain.suggested())
        except (ValueError, TypeError, ValidationError):
            return False
        return True

    def evaluate(self, request, config):
        try:
            candidate = MatchedToolAction.capture(request.action)
            value = CommandRangeConfig.from_dict(config)
        except (ValueError, TypeError, KeyError):
            return False
        original = value.action
        def fixed(action):
            bound = action.with_arguments({k: v for k, v in action.arguments.items() if k != self.COMMAND}).to_dict()
            # Do not let Python's True == 1 weaken an exact JSON condition.
            return json.dumps(bound, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if fixed(candidate) != fixed(original):
            return False
        try:
            chain = CommandChain.parse(candidate.arguments.get(self.COMMAND))
            source = CommandChain.parse(original.arguments[self.COMMAND])
        except ValueError:
            return False
        return chain.operators == source.operators and chain.matches(value.prefixes)
