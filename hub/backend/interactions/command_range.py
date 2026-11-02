"""Explicit shell argument-prefix grants for a deliberately small shell grammar.

This is a condition evaluator, not a shell runner or a risk classifier. Compound
commands retain their operators and every command must match its own prefix.
Anything outside the supported grammar requires another approval.
"""

from copy import deepcopy
from dataclasses import dataclass
import json
import shlex
from jsonschema import ValidationError

from llm.components.tools.builtin.process_values import ShellArguments
from llm.core.approval import ApprovalCondition
from llm.services.runtime.approval import ApprovalEvaluator


@dataclass(frozen=True, slots=True)
class CommandChain:
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
        if not isinstance(command, str) or any(c in cls.UNSUPPORTED or ord(c) < 32 and c != '\t'
                                               for c in command):
            raise ValueError('Unsupported shell syntax for a command-range rule')
        # A punctuation token inside quotes must not be mistaken for an operator.
        # Only plain words and simple quotes containing no shell punctuation are
        # accepted; shlex subsequently preserves quoted whitespace as one argv.
        quote = None
        for char in command:
            if char in "\"'":
                if quote == char:
                    quote = None
                elif quote is None:
                    quote = char
            elif quote and char in '|&;':
                raise ValueError('Quoted shell operators require exact approval')
        lexer = shlex.shlex(command, posix=True, punctuation_chars='|&;')
        lexer.whitespace_split = True
        lexer.commenters = ''
        commands, operators, current = [], [], []
        for token in lexer:
            if token and all(c in '|&;' for c in token):
                if token not in cls.OPERATORS or not current:
                    raise ValueError('Unsupported command separator')
                commands.append(tuple(current))
                operators.append(token)
                current = []
            else:
                current.append(token)
        if not current:
            raise ValueError('A complete command is required')
        commands.append(tuple(current))
        if any(not args[0] or '=' in args[0] or args[0] in cls.RESERVED for args in commands):
            raise ValueError('Shell assignments and control flow require exact approval')
        return cls(tuple(commands), tuple(operators))

    def matches(self, prefixes):
        return len(prefixes) == len(self.commands) and all(
            prefix and tuple(args[:len(prefix)]) == tuple(prefix)
            for args, prefix in zip(self.commands, prefixes))

    def suggested(self):
        # Show a concrete subcommand when present. This is only a UI suggestion;
        # it has no authority until the user explicitly accepts the displayed rule.
        return tuple(args[:2] if len(args) > 1 and not args[1].startswith('-') else args[:1]
                     for args in self.commands)

    @staticmethod
    def text(prefixes):
        return '\n'.join(shlex.join(prefix) for prefix in prefixes)

    @classmethod
    def prefixes(cls, text):
        result = []
        for line in text.splitlines():
            chain = cls.parse(line)
            if chain.operators:
                raise ValueError('Write one command prefix per line')
            result.append(chain.commands[0])
        return tuple(result)


class CommandRangeApproval(ApprovalEvaluator):
    IMPLEMENTATION = 'hub-shell-command-range'
    REVISION = '1'
    COMMAND = ShellArguments.command.__name__

    def __init__(self):
        super().__init__(revision=self.REVISION)

    def describe_config(self):
        return {'type': 'object', 'additionalProperties': False,
            'required': ['action', 'prefixes'], 'properties': {
                'action': {'type': 'object', 'additionalProperties': False,
                    'required': ['tool', 'arguments', 'contract', 'policy'], 'properties': {
                        'tool': {'const': ShellArguments.NAME},
                        'arguments': {'type': 'object', 'required': [self.COMMAND],
                                      'properties': {self.COMMAND: {'type': 'string'}}},
                        'contract': {'type': 'object'}, 'policy': {'type': 'object'}}},
                'prefixes': {'type': 'array', 'minItems': 1, 'items': {
                    'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}}}}

    def validate_config(self, config):
        super().validate_config(config)
        chain = CommandChain.parse(config['action']['arguments'][self.COMMAND])
        if not chain.matches(config['prefixes']):
            raise ValueError('Every prefix must match its original command')

    @classmethod
    def condition(cls, action, prefixes):
        config = {'action': deepcopy(action), 'prefixes': [list(p) for p in prefixes]}
        cls().validate_config(config)
        return ApprovalCondition(cls.IMPLEMENTATION, cls.REVISION, config)

    @classmethod
    def config(cls, condition):
        if (condition.implementation, condition.revision) != (cls.IMPLEMENTATION, cls.REVISION):
            return None
        cls().validate_config(condition.config)
        return deepcopy(condition.config)

    @classmethod
    def available(cls, action):
        if action.get('tool') != ShellArguments.NAME:
            return False
        try:
            chain = CommandChain.parse(action['arguments'].get(cls.COMMAND))
            cls.condition(action, chain.suggested())
        except (ValueError, TypeError, ValidationError):
            return False
        return True

    def evaluate(self, request, config):
        candidate = request.action
        original = config['action']
        if not isinstance(candidate, dict) or not all(key in candidate for key in original):
            return False
        arguments = candidate.get('arguments')
        if not isinstance(arguments, dict):
            return False
        def fixed(action):
            bound = {key: action[key] for key in original}
            bound['arguments'] = {k: v for k, v in action['arguments'].items() if k != self.COMMAND}
            # Do not let Python's True == 1 weaken an exact JSON condition.
            return json.dumps(bound, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if fixed(candidate) != fixed(original):
            return False
        try:
            chain = CommandChain.parse(arguments.get(self.COMMAND))
            source = CommandChain.parse(original['arguments'][self.COMMAND])
        except ValueError:
            return False
        return chain.operators == source.operators and chain.matches(config['prefixes'])
