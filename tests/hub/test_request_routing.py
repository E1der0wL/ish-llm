"""Request-owned Tool narrowing preserves Agent selection and native admission."""

import asyncio
from dataclasses import replace
import json
import tempfile
from types import SimpleNamespace as NS
import unittest

from hub.asset.guides import SYSTEM_PROMPT, WORK_GUIDANCE, CONVERSATION_PROMPT
from hub.backend.execution.request_profile import RequestMode, RequestProfile
from hub.backend.execution.request_routing import RequestRouting
from hub.backend.execution.request_tools import RequestTools, RequestToolSet
from hub.backend.execution.tool_catalog import ToolCatalog, ToolTag
from hub.backend.execution.tool_lookup import ToolLookup
from hub.backend.execution.retries import HubLoopEngine
from hub.backend.execution.environment import EnvironmentEngine
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.environment import ENVIRONMENT_OPTION, ShellEnvironment
from llm.components.tools import Tool, ToolRegistry
from llm.core.tool_contracts import ToolEffect
from llm.core.configuration import UNSET
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.support.runtime_tools import TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, chunk, call


def selected_tools(tools, mode, *, extra_groups=None):
    return tools.select(RequestTools.names(ToolCatalog.from_registry(tools, extra_groups=extra_groups), mode))


class RequestProfileTests(unittest.TestCase):
    def test_component_tags_cover_exporters_and_declared_dynamic_aliases(self):
        async def handler(arguments):
            return arguments
        examples = {
            'memory_get': ToolTag.MEMORY, 'goal_create': ToolTag.GOAL,
            'refinement_read': ToolTag.REFINEMENT, 'skill_read_resource': ToolTag.SKILL,
            'agent_run': ToolTag.AGENT, 'agent_schema': ToolTag.AGENT,
            'workflow_read': ToolTag.WORKFLOW, 'image_ocr': ToolTag.VISION,
            'prompt_read': ToolTag.PROMPT, 'hub_help': ToolTag.APPLICATION,
        }
        registry = ToolRegistry(tuple(Tool(name, '', {'type': 'object'}, handler,
            contract=TEST_CONTRACT) for name in (*examples, 'company_lookup', 'package_tool', 'unrelated')))
        from llm.core.models import ProjectConfig
        project = NS(components=['mcp', 'tools'], config=ProjectConfig(parameters={'components': {
            'mcp': {'config': {'tools': {'company_lookup': {'server': 'company', 'tool': 'remote-name'}}}},
            'tools': {'config': {'enabled': ['package_tool']}},
        }}))
        groups = ToolCatalog.project_groups(project)
        catalog = ToolCatalog.from_registry(registry, extra_groups=groups)
        tags = {entry.name: entry.tags for entry in catalog.entries}
        for name, tag in examples.items():
            self.assertIn(tag, tags[name])
        self.assertIn(ToolTag.MCP, tags['company_lookup'])
        self.assertIn(ToolTag.TOOL, tags['package_tool'])
        self.assertTrue(all(ToolCatalog.unclassified(entry) for entry in catalog.entries
            if entry.name in ('company_lookup', 'package_tool')))
        self.assertTrue(ToolCatalog.unclassified(next(e for e in catalog.entries if e.name == 'unrelated')))

    def test_discovery_falls_back_without_granting_execution_or_reading_hidden_tools(self):
        async def handler(arguments):
            raise AssertionError('Discovery must not invoke candidate handlers')
        def tool(name, description, effect):
            return Tool(name, description, {'type': 'object'}, handler,
                contract=replace(TEST_CONTRACT, effect=effect))
        allowed = ToolRegistry((tool('file_read', 'Read a file', ToolEffect.READ_ONLY),
            tool('company_export', 'Export a report', ToolEffect.UNKNOWN),
            tool('read_unknown', 'Read a report', ToolEffect.READ_ONLY),
            *(tool('opaque_' + str(i), 'Custom operation', ToolEffect.UNKNOWN) for i in range(8))))
        selected = selected_tools(allowed, RequestMode.LOOKUP)
        lookup = ToolLookup(allowed)
        current = lookup.search('read_unknown')
        self.assertEqual(current['scope'], 'matching')
        self.assertTrue(current['tools'][0]['unclassified'])
        fallback = lookup.search('company_export')
        self.assertEqual(fallback['scope'], 'matching')
        self.assertTrue(fallback['tools'][0]['unclassified'])
        self.assertEqual(fallback['tools'][0]['parameters'], {'type': 'object'})
        self.assertEqual(len(lookup.search('')['tools']), ToolLookup.PAGE_SIZE)
        self.assertIsNotNone(lookup.search('')['next_offset'])
        self.assertFalse(lookup.search('nonsense')['tools'][0].get('parameters'))
        self.assertFalse(lookup.search('', 100)['tools'])
        restricted = allowed.select(('file_read',))
        self.assertFalse(ToolLookup(restricted).search('company_export')['tools'])
        self.assertNotIn('company_export', selected.names())

    def test_conservative_classification_and_fresh_message_context(self):
        for text in ('안녕?', 'Thank you!', 'HELLO.'):
            self.assertEqual(RequestProfile.classify(text).mode, RequestMode.CONVERSATION)
        for text in ('이 명령의 역할을 알려줘', '이 자료를 요약해줘', 'Explain the output',
                     'Find relevant RAG documents', '왜 이런 에러야?'):
            self.assertEqual(RequestProfile.classify(text).mode, RequestMode.LOOKUP)
        for text in ('안녕? 파일 만들어줘', 'Explain and fix this bug', '계속해',
                     'Please run the tests', '이 자료를 요약해줘' * 1000):
            self.assertEqual(RequestProfile.classify(text).mode, RequestMode.WORK)
        source = RequestProfile.capture('hello', {'other': 'kept'})
        fresh = RequestProfile.capture('fix the bug', source)
        self.assertEqual(RequestProfile.take(fresh).mode, RequestMode.WORK)
        self.assertEqual(fresh, {'other': 'kept'})
        self.assertEqual(RequestProfile.take(source).mode, RequestMode.CONVERSATION)

    def test_unknown_profile_is_rejected_and_absence_does_not_route(self):
        self.assertIsNone(RequestProfile.take({}))
        for value in (None, {}, {'mode': 'unknown'}, {'mode': 'lookup', 'extra': True}):
            with self.assertRaisesRegex(ValueError, RequestProfile.INVALID):
                RequestProfile.take({RequestProfile.KEY: value})

    def test_custom_backend_and_mixed_recipients_keep_context_ownership(self):
        wrapped, raw = EnvironmentEngine(LoopEngine(), None), LoopEngine()
        self.assertIn(RequestProfile.KEY, RequestProfile.for_engines('hello', UNSET, [wrapped]))
        for engines in ([raw], [wrapped, raw]):
            self.assertIs(RequestProfile.for_engines('hello', UNSET, engines), UNSET)
            self.assertEqual(RequestProfile.for_engines('hello', {'custom': 1}, engines), {'custom': 1})
            context = RequestProfile.capture('hello', {'custom': 1})
            self.assertEqual(RequestProfile.for_engines('explain', context, engines), {'custom': 1})
            self.assertIn(RequestProfile.KEY, context)

    def test_graph_custom_nested_and_explicit_tool_requirement_keep_original_selection(self):
        class CustomLoop(LoopEngine):
            pass
        routing = RequestRouting({'engine': 'loop'}, RequestProfile(RequestMode.CONVERSATION))
        context = NS(output_step_id=None, messages=[NS(id='u', content='hello')],
                     run=NS(input_message_id='u'))
        self.assertEqual(routing.mode(context, LoopEngine()), RequestMode.CONVERSATION)
        for engine in (CustomLoop(), GraphEngine(handlers={})):
            self.assertEqual(routing.mode(context, engine), RequestMode.WORK)
        context.output_step_id = 'node'
        self.assertEqual(routing.mode(context, LoopEngine()), RequestMode.WORK)
        context.output_step_id = None
        routing.definition['policy'] = {'require_tool': True}
        self.assertEqual(routing.mode(context, LoopEngine()), RequestMode.WORK)
        routing.definition['policy'] = {}
        context.messages[0].content = 'create a file'
        self.assertEqual(routing.mode(context, LoopEngine()), RequestMode.WORK)

    def test_tool_selection_uses_trusted_effect_and_never_adds_missing_shell(self):
        async def handler(arguments):
            return arguments
        def tool(name, effect):
            return Tool(name, '', {'type': 'object'}, handler,
                        contract=replace(TEST_CONTRACT, effect=effect))
        registry = ToolRegistry(tuple(tool(name, effect) for name, effect in (
            ('rag_search', ToolEffect.READ_ONLY), ('file_read', ToolEffect.READ_ONLY),
            ('custom_read', ToolEffect.READ_ONLY), ('misleading_read_name', ToolEffect.EXTERNAL),
            ('file_create', ToolEffect.EXTERNAL), ('unknown', ToolEffect.UNKNOWN),
            ('shell_execute', ToolEffect.EXTERNAL), ('ask_user', ToolEffect.UNKNOWN))))
        selected = selected_tools(registry, RequestMode.LOOKUP)
        self.assertEqual(set(selected.names()), {'rag_search', 'file_read', 'custom_read', 'shell_execute', 'ask_user'})
        self.assertEqual(selected.get('shell_execute').contract, registry.get('shell_execute').contract)
        self.assertEqual(selected_tools(registry, RequestMode.CONVERSATION).names(), ('ask_user',))
        self.assertEqual(selected_tools(registry, RequestMode.WORK).names(), registry.names())
        restricted = registry.select(('file_read',))
        self.assertEqual(selected_tools(restricted, RequestMode.LOOKUP).names(), ('file_read',))
        self.assertIn('file_create', registry.names())
        # Tag combinations use the same selector without Tool-specific branches.
        custom = RequestToolSet(all_tags=frozenset((ToolTag.READ, ToolTag.RAG)))
        self.assertEqual(custom.names(ToolCatalog.from_registry(registry)), ('rag_search',))
        self.assertFalse(custom.names(ToolCatalog.from_registry(restricted)))
        self.assertEqual(RequestTools.PROFILES[RequestMode.LOOKUP].any_tags, RequestTools.BASIC | {ToolTag.READ})
        with self.assertRaises(TypeError):
            RequestTools.PROFILES[RequestMode.LOOKUP] = custom

    def test_tags_describe_effects_and_topics_without_changing_approval_or_contracts(self):
        async def handler(arguments):
            return arguments
        def tool(name, effect, approval=False):
            return Tool(name, '', {'type': 'object'}, handler,
                contract=replace(TEST_CONTRACT, effect=effect, approval_required=approval))
        registry = ToolRegistry((tool('rag_search', ToolEffect.READ_ONLY),
            tool('rag_add_document', ToolEffect.UNKNOWN, True),
            tool('file_create', ToolEffect.EXTERNAL, True),
            tool('custom_lookup', ToolEffect.READ_ONLY),
            tool('rag_misleading_name', ToolEffect.UNKNOWN)))
        before = registry.execution_binding({})
        tags = {entry.name: entry.tags for entry in ToolCatalog.from_registry(registry).entries}
        self.assertEqual(tags['rag_search'], {ToolTag.READ, ToolTag.RAG})
        self.assertEqual(tags['rag_add_document'], {ToolTag.RAG, ToolTag.UNKNOWN_EFFECT, ToolTag.APPROVAL})
        self.assertEqual(tags['file_create'], {ToolTag.FILE, ToolTag.EFFECTFUL, ToolTag.APPROVAL})
        self.assertEqual(tags['rag_misleading_name'], {ToolTag.UNKNOWN_EFFECT})
        read_rag = RequestToolSet(all_tags=frozenset((ToolTag.RAG, ToolTag.READ)))
        self.assertEqual(read_rag.names(ToolCatalog.from_registry(registry)), ('rag_search',))
        self.assertEqual(RequestToolSet().names(ToolCatalog.from_registry(registry)), ())
        self.assertEqual(registry.execution_binding({}), before)
        extended = ToolCatalog.from_registry(registry, extra_groups={ToolTag.RAG: ('custom_lookup',)})
        self.assertIn(ToolTag.RAG, next(item.tags for item in extended.entries if item.name == 'custom_lookup'))
        self.assertEqual(set(read_rag.names(ToolCatalog.from_registry(registry, extra_groups={ToolTag.RAG: ('custom_lookup', 'missing')}))),
                         {'rag_search', 'custom_lookup'})
        class FalseyGroups(dict):
            def __bool__(self):
                return False
        self.assertIn('custom_lookup', read_rag.names(ToolCatalog.from_registry(registry,
            extra_groups=FalseyGroups({ToolTag.RAG: ('custom_lookup',)}))))
        for tag in ToolCatalog.DERIVED_TAGS:
            with self.assertRaisesRegex(ValueError, ToolCatalog.INVALID_GROUP):
                ToolCatalog.from_registry(registry, extra_groups={tag: ('file_create',)})


class RoutingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_help_discovers_current_tool_in_same_run_and_preserves_plain_help(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({'tool_query': 'file_read'}), name='hub_help')], finish='tool_calls')],
            [chunk('Found the reader.', finish='stop')],
            [chunk(calls=[call('{}', name='hub_help')], finish='tool_calls')],
            [chunk('Here is the help.', finish='stop')])
        runtime, sid = await self.setup_runtime(provider)
        await self.submit(runtime, sid, 'Find a tool to explain a file')
        result = next(m['content'] for m in provider.requests[1]['messages'] if m['role'] == 'tool')
        self.assertIn('file_read', result)
        self.assertIn('next model call', result)
        await self.submit(runtime, sid, 'Explain Hub usage')
        result = next(m['content'] for m in reversed(provider.requests[-1]['messages']) if m['role'] == 'tool')
        self.assertIn('hub_help', result)

    async def test_discovery_exposes_unclassified_tool_for_next_call_in_same_run(self):
        from tests.hub.test_tool_retry import ExampleTools
        invoked = []
        async def handler(arguments):
            invoked.append(arguments)
            return 'converted'
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({'tool_query': 'convert'}), name='hub_help')], finish='tool_calls')],
            [chunk(calls=[call(json.dumps({'format': 'new', 'count': 1}), name='convert', call_id='convert-call')], finish='tool_calls')],
            [chunk('Converted.', finish='stop')])
        runtime, sid = await self.setup_runtime(provider, component_factories=(lambda: ExampleTools(handler,
            contract=replace(TEST_CONTRACT, effect=ToolEffect.UNKNOWN)),))
        await self.submit(runtime, sid, 'Find a tool for format conversion')
        self.assertNotIn('convert', self.tools(provider.requests[0]))
        content = next(m['content'] for m in provider.requests[1]['messages'] if m['role'] == 'tool')
        self.assertIn('convert', content)
        self.assertIn('unclassified', content)
        self.assertIn('next model call', content)
        self.assertIn('convert', self.tools(provider.requests[1]))
        self.assertIn('convert', self.tools(provider.requests[2]))
        self.assertEqual(invoked, [{'format': 'new', 'count': 1}])
        self.assertEqual(len(await runtime.sessions[sid].run.alist()), 1)

    async def setup_runtime(self, provider, *, root=None, **kwargs):
        if root is None:
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            root = directory.name
        runtime = HubRuntime(HubConfig(root, model='test/routing', auto_title=False,
            engine_factories={'loop': lambda: HubLoopEngine(
                completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}, **kwargs))
        self.addAsyncCleanup(runtime.close)
        await runtime.start()
        sid = await runtime.new_session()
        return runtime, sid

    async def submit(self, runtime, sid, text, *, agent_id='assistant', environment=True):
        kwargs = {}
        if environment:
            kwargs['request_context'] = {ENVIRONMENT_OPTION: ShellEnvironment.from_dict({
                'cwd': str(runtime.config.workspace), 'env': {'PWD': str(runtime.config.workspace)}}).to_dict()}
        identifier = await runtime.submit(sid, text, agent_id=agent_id, **kwargs)
        await asyncio.wait_for(runtime.sessions[sid].run.wait_idle(), 60)
        snapshot = await runtime.snapshot()
        self.assertEqual(str(snapshot.run.status), 'completed', snapshot.run.error)
        return identifier

    @staticmethod
    def tools(request):
        return {item['function']['name'] for item in request.get('tools', ())}

    async def test_default_greeting_is_small_and_work_and_history_return_on_next_turn(self):
        provider = ScriptedCompletion(*[[chunk(text, finish='stop')] for text in ('First', 'Hi!', 'Third')])
        runtime, sid = await self.setup_runtime(provider)
        await self.submit(runtime, sid, 'Create the requested file.')
        await self.submit(runtime, sid, '안녕?')
        await self.submit(runtime, sid, 'Continue the work.')
        self.assertEqual(len(provider.requests), 3)
        first, greeting, last = provider.requests
        self.assertIn('file_create', self.tools(first))
        self.assertEqual(self.tools(greeting), {'ask_user'})
        self.assertLess(len(json.dumps(greeting['messages'])), 2000)
        self.assertIn(CONVERSATION_PROMPT, [m.get('content') for m in greeting['messages']])
        self.assertNotIn('First', str(greeting['messages']))
        self.assertNotIn(WORK_GUIDANCE, str(greeting['messages']))
        self.assertEqual(self.tools(first), self.tools(last))
        self.assertIn('First', str(last['messages']))
        self.assertEqual(sum(m.get('content', '').count(WORK_GUIDANCE) for m in first['messages']), 1)
        stored = await runtime.sessions[sid].aconversation()
        self.assertTrue(any(message.content == 'First' for message in stored))

    async def test_lookup_keeps_rag_files_shell_and_memory_but_omits_mutations(self):
        provider = ScriptedCompletion([chunk('Here is the explanation.', finish='stop')])
        runtime, sid = await self.setup_runtime(provider)
        await self.submit(runtime, sid, '이 명령어가 무엇인지 알려줘')
        names = self.tools(provider.requests[0])
        self.assertTrue({'rag_search', 'file_read', 'shell_execute', 'ask_user'} <= names, names)
        self.assertFalse({'file_create', 'file_delete', 'hub_task_result', 'memory_delete'} & names, names)

    async def test_explicit_agent_prompt_selection_and_reasoning_are_preserved(self):
        provider = ScriptedCompletion([chunk('Hello!', finish='stop')], [chunk('Answer', finish='stop')])
        runtime, sid = await self.setup_runtime(provider)
        await runtime.project.agents.acreate({'engine': 'loop',
            'completion': {'model': 'test/custom', 'reasoning_effort': 'low'},
            'system_prompt': 'CUSTOM SAFETY AND IDENTITY', 'tools': ['file_read']}, identifier='reader')
        await runtime.invite_agent(runtime.project.id, sid, 'reader')
        await self.submit(runtime, sid, 'hello', agent_id='reader', environment=False)
        await self.submit(runtime, sid, 'Explain this file', agent_id='reader', environment=False)
        self.assertFalse(self.tools(provider.requests[0]))
        self.assertIn('CUSTOM SAFETY AND IDENTITY', str(provider.requests[0]['messages']))
        self.assertEqual(provider.requests[0]['reasoning_effort'], 'low')
        self.assertEqual(self.tools(provider.requests[1]), {'file_read'})

    async def test_final_review_still_requires_answer_content_with_question_tool_only(self):
        provider = ScriptedCompletion(
            [{'choices': [{'index': 0, 'delta': {'reasoning_content': 'thinking'}, 'finish_reason': 'stop'}]}],
            [chunk('Hello!', finish='stop')])
        runtime, sid = await self.setup_runtime(provider)
        await self.submit(runtime, sid, 'hello')
        self.assertEqual(len(provider.requests), 2)
        self.assertTrue(all(self.tools(request) == {'ask_user'} for request in provider.requests))

    async def test_selected_resource_instructions_survive_brief_default_prompt(self):
        provider = ScriptedCompletion([chunk('Hello!', finish='stop')])
        runtime, sid = await self.setup_runtime(provider)
        prompts = await runtime.project.components.aget('prompts')
        await prompts.acreate({'messages': [{'role': 'system', 'content': 'EXPLICIT RESOURCE INSTRUCTION'}]},
                              identifier='greeting-resource')
        await runtime.project.agents.acreate({'engine': 'loop',
            'completion': {'model': 'test/resource'}, 'system_prompt': SYSTEM_PROMPT,
            'resources': {'prompt': 'greeting-resource'}}, identifier='resource-reader')
        await runtime.invite_agent(runtime.project.id, sid, 'resource-reader')
        await self.submit(runtime, sid, 'hello', agent_id='resource-reader')
        text = '\n'.join(m.get('content', '') for m in provider.requests[0]['messages'])
        self.assertIn('EXPLICIT RESOURCE INSTRUCTION', text)
        self.assertIn(CONVERSATION_PROMPT, text)
        self.assertNotIn(WORK_GUIDANCE, text)
        # An Agent with no selected Tools must not gain even the base question Tool.
        self.assertFalse(self.tools(provider.requests[0]))

    async def test_question_is_usable_in_brief_conversation_without_starting_another_run(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({'question': 'How should I address you?'}), name='ask_user')], finish='tool_calls')],
            [chunk('Hello, Sam!', finish='stop')])
        runtime, sid = await self.setup_runtime(provider)
        await runtime.submit(sid, 'hello')
        async with asyncio.timeout(60):
            while not runtime.builtin_tools.questions.pending:
                await asyncio.sleep(0.05)
        item, = (await runtime.snapshot()).questions
        await runtime.answer_question(runtime.project.id, sid, item['run_id'], item['request'], 'reply', 'Sam')
        await asyncio.wait_for(runtime.sessions[sid].run.wait_idle(), 60)
        self.assertEqual((await runtime.snapshot()).messages[-1].text, 'Hello, Sam!')
        self.assertEqual(len(await runtime.sessions[sid].run.alist()), 1)
        self.assertIn('Sam', str(provider.requests[-1]['messages']))
        self.assertTrue(all(self.tools(request) == {'ask_user'} for request in provider.requests))

    async def test_lookup_shell_still_requires_approval_and_resumes_after_restart(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = directory.name
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({'command': 'printf routed-shell'}), name='shell_execute')], finish='tool_calls')],
            [chunk('The command prints a string.', finish='stop')])
        runtime, sid = await self.setup_runtime(provider, root=root)
        await runtime.project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test/shell'},
            'tools': ['shell_execute']}, identifier='shell-reader')
        await runtime.invite_agent(runtime.project.id, sid, 'shell-reader')
        pid = runtime.project.id
        await runtime.submit(sid, 'Explain the command output', agent_id='shell-reader', request_context={
            ENVIRONMENT_OPTION: ShellEnvironment.from_dict({'cwd': root, 'env': {'PWD': root}}).to_dict()})
        await asyncio.wait_for(runtime.sessions[sid].run.wait_idle(), 60)
        handle = (await runtime.sessions[sid].run.alist())[-1]
        paused = await handle.aget_data()
        self.assertEqual(str(paused.status), 'paused', paused.error)
        interaction, = await handle.ainteractions()
        await handle.arespond(interaction.respond('approve'))
        config = runtime.config
        await runtime.close()
        reopened = HubRuntime(config)
        self.addAsyncCleanup(reopened.close)
        await reopened.start()
        project = await reopened.backend.projects.aload(pid)
        session = await project.sessions.aload(sid)
        done, = await (await session.run.resume(paused.id, agent_id='shell-reader')).wait(timeout=60)
        self.assertEqual(str(done.data.status), 'completed', done.data.error)
        self.assertEqual(self.tools(provider.requests[0]), {'shell_execute'})
        self.assertEqual(self.tools(provider.requests[1]), {'shell_execute'})
        steps = await done.steps.alist()
        self.assertEqual(sum(step.kind == 'tool' for step in steps), 1)

    async def test_steering_from_greeting_restores_work_tools_prompt_and_history_in_same_run(self):
        from tests.llm.test_loop_steering import Model
        provider = Model([chunk('Hello!', finish='stop')], [chunk('Task acknowledged.', finish='stop')])
        self.addCleanup(provider.release.set)
        runtime, sid = await self.setup_runtime(provider)
        await runtime.submit(sid, 'hello', agent_id='assistant', request_context={
            ENVIRONMENT_OPTION: ShellEnvironment.from_dict({
                'cwd': str(runtime.config.workspace), 'env': {}}).to_dict()})
        self.assertTrue(await asyncio.to_thread(provider.entered.wait, 60))
        handle, = await runtime.sessions[sid].run.alist()
        await runtime.sessions[sid].run.steer(handle.id, 'Create a file for the new task')
        provider.release.set()
        await asyncio.wait_for(runtime.sessions[sid].run.wait_idle(), 60)
        self.assertEqual(str((await handle.aget_data()).status), 'completed')
        self.assertEqual(self.tools(provider.requests[0]), {'ask_user'})
        self.assertIn('file_create', self.tools(provider.requests[1]))
        self.assertNotIn(WORK_GUIDANCE, str(provider.requests[0]['messages']))
        self.assertIn(SYSTEM_PROMPT, [m.get('content') for m in provider.requests[1]['messages']])
        self.assertIn('Hello!', str(provider.requests[1]['messages']))
        self.assertEqual(len(await runtime.sessions[sid].run.alist()), 1)

    async def test_discovered_tool_approval_resume_reuses_search_and_does_not_repeat_effect(self):
        from tests.hub.test_tool_retry import ExampleTools
        effects = []
        async def convert(arguments):
            effects.append(arguments)
            return 'converted once'
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"tool_query":"convert"}', name='hub_help')], finish='tool_calls')],
            [chunk(calls=[call('{"format":"new","count":1}', name='convert', call_id='convert-call')], finish='tool_calls')],
            [chunk('The conversion is complete.', finish='stop')])
        runtime, sid = await self.setup_runtime(provider, component_factories=(lambda: ExampleTools(convert,
            contract=replace(TEST_CONTRACT, effect=ToolEffect.EXTERNAL, approval_required=True)),))
        await runtime.submit(sid, 'Find the conversion tool', agent_id='assistant')
        await asyncio.wait_for(runtime.sessions[sid].run.wait_idle(), 60)
        handle, = await runtime.sessions[sid].run.alist()
        self.assertEqual(str((await handle.aget_data()).status), 'paused')
        self.assertEqual(effects, [])
        interaction, = await handle.ainteractions()
        await handle.arespond(interaction.respond('approve'))
        done, = await (await runtime.sessions[sid].run.resume(handle.id, agent_id='assistant')).wait(timeout=60)
        self.assertEqual(str(done.data.status), 'completed', done.data.error)
        self.assertEqual(effects, [{'format': 'new', 'count': 1}])
        self.assertEqual(len(provider.requests), 3)
        self.assertNotIn('convert', self.tools(provider.requests[0]))
        self.assertIn('convert', self.tools(provider.requests[1]))
        self.assertIn('convert', self.tools(provider.requests[2]))
        steps = await done.steps.alist()
        tool_steps = [step for step in steps if step.kind == 'tool']
        self.assertEqual(len(tool_steps), 2)

    async def test_steering_narrows_next_call_without_losing_completed_tool_result(self):
        from tests.llm.test_loop_steering import Model
        provider = Model(
            [chunk(calls=[call('{}', name='hub_help')], finish='tool_calls')],
            [chunk('Here is the explanation.', finish='stop')])
        self.addCleanup(provider.release.set)
        runtime, sid = await self.setup_runtime(provider)
        await runtime.submit(sid, 'Create the requested document', agent_id='assistant')
        self.assertTrue(await asyncio.to_thread(provider.entered.wait, 60))
        handle, = await runtime.sessions[sid].run.alist()
        await runtime.sessions[sid].run.steer(handle.id, 'Instead explain the existing document')
        provider.release.set()
        await asyncio.wait_for(runtime.sessions[sid].run.wait_idle(), 60)
        self.assertEqual(str((await handle.aget_data()).status), 'completed')
        self.assertIn('file_create', self.tools(provider.requests[0]))
        self.assertNotIn('file_create', self.tools(provider.requests[1]))
        self.assertIn('file_read', self.tools(provider.requests[1]))
        self.assertTrue(any(m['role'] == 'tool' for m in provider.requests[1]['messages']))
        checkpoint = await handle.acheckpoint('loop')
        self.assertIn('file_create', checkpoint['records']['iteration:1']['tools'])
        self.assertNotIn('file_create', checkpoint['records']['iteration:2']['tools'])
