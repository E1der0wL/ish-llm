"""Cross-check the policy schema owner and actual runtime readers.

This catches a declared limit disappearing from the runtime, independently of
literal inventories. Runtime effects and Resume are tested by the R3 regression.
"""

import ast
import inspect
import textwrap
import unittest

from llm.core.models import ProjectConfig
from llm.services.runtime.tools import ToolExecutionScope


def compare_reader(source, receiver, schema):
    keys = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == receiver and node.attr != 'get'):
            keys.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == receiver and node.func.attr == 'get'):
            if not node.args or not isinstance(node.args[0], ast.Constant):
                raise ValueError('dynamic policy lookup needs an explicit ownership contract')
            keys.add(node.args[0].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == receiver):
            if not isinstance(node.slice, ast.Constant):
                raise ValueError('dynamic policy indexing needs an explicit ownership contract')
            keys.add(node.slice.value)
    declared = set(schema['properties'])
    return {'unconsumed': sorted(declared - keys), 'undeclared': sorted(keys - declared)}


class PolicyConsumerTests(unittest.TestCase):
    def test_tool_and_retry_readers_consume_their_own_complete_schema(self):
        schemas = ProjectConfig.describe_policies()['properties']
        source = inspect.getsource(ToolExecutionScope.__init__)
        for receiver, section in (('policy', 'tools'), ('retry', 'tool_retry')):
            with self.subTest(section=section):
                self.assertEqual(compare_reader(source, receiver, schemas[section]),
                                 {'unconsumed': [], 'undeclared': []})

    def test_deleted_limit_reader_and_unknown_field_are_detected(self):
        schema = {'properties': {'max_calls': {}, 'allowed_tools': {}}}
        valid = "limit = policy.get('max_calls')\ntools = policy['allowed_tools']"
        self.assertEqual(compare_reader(valid, 'policy', schema), {'unconsumed': [], 'undeclared': []})
        self.assertEqual(compare_reader("tools = policy['allowed_tools']", 'policy', schema)['unconsumed'],
                         ['max_calls'])
        self.assertEqual(compare_reader(valid + "\nx = policy.get('typo')", 'policy', schema)['undeclared'],
                         ['typo'])

    def test_dynamic_access_is_not_treated_as_a_complete_proof(self):
        for source in ('value = policy.get(key)', 'value = policy[key]'):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'dynamic policy'):
                compare_reader(source, 'policy', {'properties': {}})

    def test_typed_reader_still_detects_deleted_limits_and_unknown_attributes(self):
        schema = {'properties': {'max_calls': {}, 'allowed_tools': {}}}
        valid = 'limit = policy.max_calls\ntools = policy.allowed_tools'
        self.assertEqual(compare_reader(valid, 'policy', schema), {'unconsumed': [], 'undeclared': []})
        self.assertEqual(compare_reader('tools = policy.allowed_tools', 'policy', schema)['unconsumed'], ['max_calls'])
        self.assertEqual(compare_reader(valid + '\nx = policy.typo', 'policy', schema)['undeclared'], ['typo'])
