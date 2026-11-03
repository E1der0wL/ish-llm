"""Thought previews stay bounded without changing Tool or instruction evidence."""

from copy import deepcopy
from unittest import TestCase

from prompt_toolkit.utils import get_cwidth
from llm.core.execution_observations import ExecutionObservations
from llm.core.results import EngineOutput
from hub.backend.execution.tool_details import ToolDetails
from hub.config.theme import HubTheme
from hub.config.theme_style import ThemeSelector
from hub.locales import Language
from hub.locales.keys import MessageKey
from hub.model import TraceItem, InstructionDisplay
from hub.widget.transcript.thought_trace import render_trace_item
from hub.widget.transcript.instruction_caption import instruction_caption


class ExecutionPreviewTests(TestCase):
    def render(self, item, width=80):
        rows = render_trace_item(item, width, HubTheme(), Language('ko'))
        content = [row for row in rows if any(text.strip() for _, text in row)]
        for row in content:
            self.assertTrue(all(style == ThemeSelector.MUTED.style for style, _ in row))
            self.assertLessEqual(get_cwidth(''.join(text for _, text in row)), width)
        return [''.join(text for _, text in row).strip() for row in content]

    def test_long_tool_arguments_remain_one_row_and_output_is_at_most_three_more(self):
        item = TraceItem('s', TraceItem.TOOL, 'shell_execute', 'running', details=(
            (MessageKey.CARD_INPUT, 'python3 ' + '긴경로/파일.py ' * 150),
            (MessageKey.TOOL_RESULT, '결과 한글 English ' * 200),
        ))
        for width in (6, 24, 80, 140):
            with self.subTest(width=width):
                rows = self.render(item, width)
                self.assertEqual(len(rows), 4)
                self.assertTrue(rows[0].endswith('…'))
                self.assertTrue(rows[-1].endswith('…'))
        self.assertIn('결과', self.render(item)[1])
        no_result = TraceItem('s', TraceItem.TOOL, 'shell_execute', 'running', details=item.details[:1])
        self.assertEqual(len(self.render(no_result)), 1)

    def test_questions_description_answer_are_separate_and_optional(self):
        item = TraceItem('q', TraceItem.QUESTION, 'ask_user', 'completed', details=(
            (MessageKey.QUESTION_HEADING, '어떤 **형식**인가요?'),
            (MessageKey.CARD_DESCRIPTION, '표와 설명을 함께 저장합니다.'),
            (MessageKey.CARD_ANSWER, '**Markdown**으로 저장해줘.'),
        ))
        self.assertEqual(self.render(item), ['어떤 형식인가요?', '표와 설명을 함께 저장합니다.', 'Markdown으로 저장해줘.'])
        pending = TraceItem('q', TraceItem.QUESTION, 'ask_user', 'waiting_input', details=item.details[:1])
        self.assertEqual(self.render(pending), ['어떤 형식인가요?'])

    def test_compact_projection_does_not_change_raw_evidence_or_judge_payload(self):
        data = {'arguments': {'command': 'python3 -m unittest', 'cwd': '/project', 'env': {'X': 'original'}}}
        ExecutionObservations.store_output(data, EngineOutput(data={'stdout': 'Ran 8 tests\nOK', 'returncode': 0}))
        original = deepcopy(data)
        judge_before = ToolDetails.project(data)
        summary = dict(ToolDetails.project(data, compact=True))
        self.assertEqual(summary[MessageKey.CARD_INPUT], 'python3 -m unittest')
        self.assertEqual(summary[MessageKey.TOOL_RESULT], 'Ran 8 tests OK')
        self.assertEqual(data, original)
        self.assertEqual(ToolDetails.project(data), judge_before)
        self.assertIn('original', str(judge_before))

    def test_unknown_tools_and_errors_use_generic_values_without_json_or_ansi(self):
        metadata = {'arguments': {'custom': 'value', 'enabled': False}}
        ExecutionObservations.store_output(metadata, EngineOutput(data={'count': 8, 'items': ['a', 'b']}))
        details = dict(ToolDetails.project(metadata, error={'message': 'failed\x1b[2J'}, compact=True))
        self.assertIn('custom: value', details[MessageKey.CARD_INPUT])
        self.assertIn('enabled: False', details[MessageKey.CARD_INPUT])
        self.assertIn('count: 8', details[MessageKey.TOOL_RESULT])
        item = TraceItem('t', TraceItem.TOOL, 'plugin_tool', 'failed', details=tuple(details.items()))
        rows = self.render(item)
        self.assertIn('failed', rows[1])
        self.assertNotIn('count', '\n'.join(rows))
        self.assertNotIn('\x1b', '\n'.join(rows))

    def test_pending_instruction_caption_uses_single_status(self):
        language = Language('ko')
        self.assertEqual(instruction_caption(InstructionDisplay('i', 'hello', 'pending'), language),
                         language.icons.for_status('pending') + ' 반영 대기')
