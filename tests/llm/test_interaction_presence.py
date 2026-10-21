"""Explicit approval inputs preserve identity, source, action and effect semantics."""

from copy import deepcopy
from dataclasses import replace
import unittest

from llm.core.interactions import InteractionOption, InteractionRequest, approval_request
from llm.services.runtime.tools import ToolApprovalRequired


class FalseyMapping(dict):
    def __bool__(self):
        return False


class FalseyRequest(InteractionRequest):
    def __bool__(self):
        return False


class InteractionPresenceTests(unittest.TestCase):
    def test_explicit_falsey_request_is_not_replaced_by_fresh_approval(self):
        request = FalseyRequest('Explicit review',
            (InteractionOption('yes', 'Allow', effect='approve', value=True),
             InteractionOption('no', 'Deny', effect='deny', value=False)),
            source={'run_id':'run'}, action={'tool':'specific'})
        wrapped = ToolApprovalRequired(request=request).request
        self.assertEqual(wrapped.to_dict(), request.to_dict())
        self.assertEqual(wrapped.fingerprint, request.fingerprint)
        self.assertIs(wrapped.respond('no').decision(request), False)

    def test_explicit_falsey_source_and_action_are_preserved_in_fingerprint(self):
        source = FalseyMapping(run_id='run', selected=False)
        action = FalseyMapping(tool='tool', arguments={'count': 0, 'items': [], 'optional': None})
        before = deepcopy((source, action))
        request = approval_request('Review', source=source, action=action)
        self.assertEqual(request.source, dict(source))
        self.assertEqual(request.action, dict(action))
        expected = replace(request, source=dict(source), action=dict(action))
        self.assertEqual(request.fingerprint, expected.fingerprint)
        restored = InteractionRequest.from_dict(request.to_dict())
        self.assertEqual(restored.fingerprint, expected.fingerprint)
        response = request.respond('approve')
        self.assertIs(response.decision(restored), True)
        with self.assertRaisesRegex(ValueError, 'changed'):
            response.decision(replace(restored, action={'tool': 'other'}))
        self.assertEqual((source, action), before)

    def test_invalid_creation_mapping_is_not_replaced_by_empty_object(self):
        for name in ('source', 'action'):
            for value in (False, 0, '', [], (), True, 1, 'x', [1]):
                with self.subTest(name=name, value=value):
                    before = deepcopy(value)
                    with self.assertRaises(TypeError):
                        approval_request('Review', **{name:value})
                    self.assertEqual(value, before)
        for value in (None, {}):
            request = approval_request('Review', source=value, action=value)
            self.assertEqual((request.source, request.action), ({}, {}))
            self.assertEqual(InteractionRequest.from_dict(request.to_dict()), request)

    def test_custom_option_effect_remains_open_and_distinct_from_display_id(self):
        request = InteractionRequest('Choose',
            (InteractionOption('approve', 'Application action', effect='application.choice', value={'selected': False}),),
            kind='choice', category='application.custom')
        restored = InteractionRequest.from_dict(request.to_dict())
        self.assertEqual(restored, request)
        self.assertIsNone(restored.find_approval())
        self.assertEqual(restored.respond('approve').decision(restored), {'selected': False})
        approval = replace(request, options=(InteractionOption('custom-id', 'Allow', effect='approve', value=True),))
        self.assertEqual(approval.find_approval().id, 'custom-id')
