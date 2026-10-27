"""Default capabilities, fresh completion evidence and localized schema guidance."""

import asyncio
from copy import deepcopy
import json
import tempfile
import unittest

from hub.asset.guides import WORK_CYCLE
from hub.backend.execution.completion_review import TaskCompletion, ConversationalInput
from hub.backend.execution.retries import HubLoopEngine
from hub.backend.project.settings_descriptions import describe_options
from hub.backend.project.settings_help import SettingsHelp
from hub.backend.runtime import HubConfig, HubRuntime
from hub.components import COMPONENTS
from hub.config.defaults import HubDefaults
from hub.locales import Language
from hub.locales.keys import MessageKey, MessagePattern
from llm.components.review import CompletionReviewObservation, ReviewAction
from llm.components.tools import ToolRegistry
from llm.core.model_protocol import ModelMessage, ModelResponse, ToolCall, ToolResult
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class EvidenceTests(unittest.TestCase):
    def test_standalone_greetings_do_not_match_action_requests(self):
        for text in ('안녕?', ' 안녕하세요! ', 'Hello!', 'THANK YOU.'):
            self.assertTrue(ConversationalInput.matches(text))
        for text in ('안녕? 파일 만들어줘', 'Hello, fix this bug', 'Thanks, now delete it', ''):
            self.assertFalse(ConversationalInput.matches(text))

    def test_optional_report_does_not_force_an_extra_tool_for_a_plain_answer(self):
        tool = ToolCall('write', 'file_create', {'path':'answer.txt', 'content':'done'})
        messages = (ModelMessage('user', '안녕?'), ModelMessage('assistant', calls=(tool,)),
                    ModelMessage('tool', result=ToolResult('write', 'saved')))
        review = TaskCompletion.review(CompletionReviewObservation(1, messages,
            ModelResponse(ModelMessage('assistant', 'Saved.')), messages,
            ToolRegistry((TaskCompletion.tool(),)).definitions()))
        self.assertEqual(review.action, ReviewAction.ACCEPT)

    def review(self, calls, results, *, enabled=True, answer='Finished.'):
        messages = (ModelMessage('user', 'Create and verify the requested file.'),
                    ModelMessage('assistant', calls=tuple(calls)),
                    *(ModelMessage('tool', result=ToolResult(identifier, value))
                      for identifier, value in results))
        return TaskCompletion.review(CompletionReviewObservation(
            1, messages, ModelResponse(ModelMessage('assistant', answer)), messages,
            ToolRegistry((TaskCompletion.tool(),)).definitions() if enabled else ()))

    def test_report_must_follow_latest_work_without_replaying_effects(self):
        write = ToolCall('write', 'file_create', {'path': 'answer.txt', 'content': 'done'})
        report = ToolCall('report', TaskCompletion.NAME,
                          {'outcome': 'completed', 'summary': 'Saved file', 'evidence': ['write']})
        read = ToolCall('read', 'file_read', {'path': 'answer.txt'})
        stale = self.review([write, report, read], [('write','saved'), ('report','recorded'), ('read','done')])
        self.assertEqual(stale.action, ReviewAction.CONTINUE)
        self.assertEqual(stale.feedback, TaskCompletion.STALE)
        fresh = ToolCall('fresh', TaskCompletion.NAME,
                         {'outcome': 'completed', 'summary': 'Saved and read back', 'evidence': ['write','read']})
        accepted = self.review([write, report, read, fresh],
                               [('write','saved'), ('report','recorded'), ('read','done'), ('fresh','recorded')])
        self.assertEqual(accepted.action, ReviewAction.ACCEPT)

    def test_all_outcomes_validate_citations_and_explicit_opt_out_remains(self):
        for outcome in TaskCompletion.OUTCOMES:
            report = ToolCall('r', TaskCompletion.NAME,
                              {'outcome':outcome, 'summary':'Report', 'evidence':['invented']})
            self.assertEqual(self.review([report], [('r','recorded')]).action, ReviewAction.CONTINUE)
            self.assertEqual(self.review([report], [('r','recorded')], enabled=False).action, ReviewAction.ACCEPT)
        duplicate = ToolCall('r', TaskCompletion.NAME,
                             {'outcome':'completed','summary':'Report','evidence':['x','x']})
        self.assertEqual(self.review([duplicate], [('r','recorded')]).feedback, TaskCompletion.INVALID_REPORT)

    def test_empty_answer_requires_content_even_after_valid_report(self):
        report = ToolCall('r', TaskCompletion.NAME,
                         {'outcome': 'information', 'summary': 'Answered', 'evidence': []})
        for enabled in (False, True):
            for answer in ('', '  \n', '<hub-next>Explain more.</hub-next>'):
                with self.subTest(enabled=enabled, answer=answer):
                    decision = self.review([report], [('r', 'recorded')], enabled=enabled, answer=answer)
                    self.assertEqual(decision.action, ReviewAction.CONTINUE)
                    self.assertEqual(decision.feedback, TaskCompletion.EMPTY_ANSWER)
        self.assertEqual(self.review([report], [('r','recorded')],
            answer='Hello!\n<hub-next>Explain more.</hub-next>').action, ReviewAction.ACCEPT)


class SettingsGuidanceTests(unittest.TestCase):
    def test_guidance_paths_refer_to_current_public_schemas(self):
        from hub.backend.bootstrap import create_backend
        from llm.engines.loop import LoopEngine
        from llm.engines.graph import GraphEngine
        paths = []
        def collect(schema, path):
            if isinstance(schema, dict):
                paths.append(path)
                for key, child in schema.get('properties', {}).items():
                    collect(child, (*path, key))
                for keyword in ('anyOf','oneOf','allOf'):
                    for child in schema.get(keyword, ()):
                        collect(child, path)
        with tempfile.TemporaryDirectory() as root:
            backend = create_backend(HubConfig(root, auto_title=False))
            collect(backend.describe_project_config(), (SettingsHelp.PROJECT,))
        collect(LoopEngine().describe_agent(), (SettingsHelp.AGENT,))
        collect(GraphEngine(handlers={}).describe_agent(), (SettingsHelp.AGENT,))
        for pattern, _ in SettingsHelp.RULES:
            self.assertTrue(any(len(path)==len(pattern) and all(
                expected == actual or expected == SettingsHelp.ANY_NAME
                for expected, actual in zip(pattern,path)) for path in paths), pattern)

    def test_help_only_changes_descriptions_and_respects_scope(self):
        from llm.engines.loop import LoopEngine
        schema = LoopEngine().describe_agent()
        original = deepcopy(schema)
        def without_descriptions(value):
            if isinstance(value, dict):
                return {key:without_descriptions(item) for key,item in value.items() if key != 'description'}
            if isinstance(value, list):
                return [without_descriptions(item) for item in value]
            return value
        for language in ('ko','en'):
            projected = deepcopy(original)
            t = Language(language)
            describe_options(projected, t, scope=SettingsHelp.AGENT)
            self.assertEqual(without_descriptions(projected), without_descriptions(original))
            model = projected['properties']['completion']['properties']['model']['description']
            self.assertEqual(model, t(MessageKey.OPTION_HELP_MODEL))
            provider = projected['properties']['engine_options']['properties']['policy']['properties']['provider']
            self.assertEqual(provider['properties']['max_attempts']['description'], t(MessageKey.OPTION_HELP_PROVIDER_MAX_ATTEMPTS))
            arbitrary = {'properties':{'completion':{'properties':{'model':{'type':'string','description':'Custom owner meaning'}}}}}
            describe_options(arbitrary, t)
            self.assertEqual(arbitrary['properties']['completion']['properties']['model']['description'], 'Custom owner meaning')
        self.assertEqual(schema, original)

    def test_each_manager_has_localized_help_and_placeholders_are_valid(self):
        from hub.locales import en, ko
        self.assertEqual(en.MESSAGES.keys(), ko.MESSAGES.keys())
        for language in ('ko','en'):
            t = Language(language)
            for component in COMPONENTS:
                key = MessagePattern.component_help(component.name)
                text = t(key)
                self.assertNotEqual(text, key)
                self.assertGreater(len(text), 250)
                self.assertIn('## ', text)
            for key in en.MESSAGES:
                if key.startswith('option_help_'):
                    self.assertGreater(len(t(key)), 35)


class ContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def test_greeting_finishes_in_one_call_without_report_or_tools(self):
        provider = ScriptedCompletion([chunk('안녕하세요!', finish='stop')])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/greeting', auto_title=False,
                engine_factories={'loop':lambda: HubLoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            sid = await runtime.new_session()
            run, = await (await runtime.sessions[sid].run.submit(
                '안녕?', recipients=['assistant'])).wait(timeout=20)
            self.assertEqual(run.data.status, 'completed', run.data.error)
            self.assertEqual((await run.aresponse()).content, '안녕하세요!')
            self.assertEqual(len(provider.requests), 1)
            self.assertIn(ConversationalInput.INSTRUCTION, str(provider.requests[0]['messages']))
            self.assertFalse(any(step.kind == 'tool' for step in await run.steps.alist()))

    async def test_reasoning_only_completion_requests_an_answer_without_repeating_tools(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({'outcome':'information', 'summary':'Greeting reviewed',
                'evidence':[]}), name=TaskCompletion.NAME, call_id='review')], finish='tool_calls')],
            [{'choices':[{'index':0, 'delta':{'reasoning_content':'Visible thought summary'},
                          'finish_reason':'stop'}]}],
            [chunk('Hello! How can I help?', finish='stop')])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/empty-answer', auto_title=False,
                engine_factories={'loop':lambda: HubLoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                handle, = await asyncio.wait_for((await runtime.sessions[sid].run.submit(
                    'Hello', recipients=['assistant'])).wait(), 20)
                self.assertEqual(str((await handle.aresult()).status), 'completed')
                self.assertEqual(len(provider.requests), 3)
                self.assertIn(TaskCompletion.EMPTY_ANSWER, str(provider.requests[-1]['messages']))
                steps = await handle.steps.alist()
                self.assertEqual(sum(step.kind == 'tool' and step.name == TaskCompletion.NAME
                                     for step in steps), 1)
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.messages[-1].text, 'Hello! How can I help?')
                self.assertFalse(any(message.role == 'reasoning' for message in snapshot.messages))
            finally:
                await runtime.close()

    async def test_defaults_recall_latest_goals_across_turns_and_restart(self):
        provider = ScriptedCompletion()
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, model='test/continuity', auto_title=False,
                engine_factories={'loop':lambda: HubLoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
            runtime = HubRuntime(config)
            await runtime.start()
            try:
                project = runtime.project
                definition = await project.agents.aload('assistant')
                self.assertTrue({'goal_create','goal_read','goal_update_progress','goal_complete',
                    'refinement_target','refinement_evidence','refinement_propose','refinement_apply',
                    TaskCompletion.NAME,'memory_create','skill_list'}.issubset(definition['tools']))
                parameters = (await project.aget_data()).config.parameters['components']
                self.assertEqual(parameters['rag']['policy']['provider'], HubDefaults.PROVIDER)
                self.assertEqual(parameters['memory']['policy']['processing']['provider'], HubDefaults.PROVIDER)
                self.assertTrue(parameters['goals']['policy']['inject'])
                self.assertTrue(parameters['refinement']['policy']['apply_tools'])
                self.assertNotIn('embedding_params', parameters['rag']['config'])
                self.assertNotIn('completion', parameters['memory']['config']['processing'])
                sid = await runtime.new_session()
                goals = project.components.goals
                await goals.acreate({'title':'Verify change','objective':'Ship tested result',
                    'scope':{'type':'session','id':sid},'status':'active',
                    'success_criteria':['File saved','Tests pass'],
                    'progress':['File saved'], 'next_actions':['Tests pending']}, identifier='work')
                async def explain():
                    provider.responses.extend([
                        [chunk(calls=[call(json.dumps({'outcome':'information','summary':'Reviewed current goal',
                            'evidence':[]}), name=TaskCompletion.NAME,call_id='review')],finish='tool_calls')],
                        [chunk('Current progress reviewed.',finish='stop')]])
                    result, = await asyncio.wait_for((await runtime.sessions[sid].run.submit(
                        'Explain the current goal; do not execute it.',recipients=['assistant'])).wait(),15)
                    self.assertEqual(str((await result.aresult()).status),'completed')
                await explain()
                first = str(provider.requests[-1]['messages'])
                self.assertIn('Tests pending', first)
                self.assertTrue(any(WORK_CYCLE in (m['content'] or '')
                                    for m in provider.requests[-1]['messages']))
                snapshot = await goals.asnapshot('work')
                await goals.aupdate('work', {'progress':['File saved','Tests passed'],
                                          'next_actions':['Final review']}, expected_version=snapshot['version'])
                await runtime.close()
                runtime = HubRuntime(config)
                await runtime.start()
                await explain()
                latest = str(provider.requests[-1]['messages'])
                self.assertIn('Final review', latest)
                # Old transcripts may mention old progress; active references must be fresh.
                references = [m['content'] for m in provider.requests[-1]['messages']
                              if 'Active Goal Reference' in str(m.get('content',''))]
                self.assertEqual(len(references),1)
                self.assertNotIn('Tests pending', references[0])
                self.assertIn('Tests passed', references[0])
            finally:
                await runtime.close()
