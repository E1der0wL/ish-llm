"""Explicit review budgets, detached verdicts and durable candidate publication."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components.processing import CompletionPipeline, CompletionSession
from llm.components.review import CompletionReview, CompletionReviewObservation, ReviewAction, ReviewPolicy
from llm.core.model_protocol import ModelMessage, ModelResponse
from llm.core.models import RunStatus
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from tests.llm.test_common_provider_acceptance import IndependentAdapter
from tests.llm.test_completion_processing import Feature, Processor
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from llm.components.tools import Tool, ToolRegistry


# Explicit Application choices for these tests; never product defaults.
POLICY = {"max_continuations": 2, "max_attempts": 2, "hook_timeout": 5, "max_feedback_chars": 256}


def answer(text, *, seal=None, usage=2):
    return [{"words": text, "seal": {} if seal is None else seal, "end": "done",
             **({"tally": {"total_tokens": usage}} if usage is not None else {})}]


def reviewer(factory, name="reviewer", revision="v1", priority=0):
    value = Processor(name, factory, priority)
    value.review_revision = revision
    return value


class ReviewContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_policy_requires_every_budget_and_rejects_bool_versions(self):
        self.assertIsNone(ReviewPolicy.read(None))
        self.assertEqual(ReviewPolicy.read(POLICY), POLICY)
        for field in POLICY:
            value = deepcopy(POLICY);del value[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                ReviewPolicy.read(value)
        for field in POLICY:
            for value in (True, False, None, "1", -1):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    ReviewPolicy.read({**POLICY, field:value})
        self.assertEqual(ReviewPolicy.read({**POLICY,"max_continuations":0})["max_continuations"],0)
        with self.assertRaises(TypeError):
            CompletionReview()
        with self.assertRaises(TypeError):
            CompletionReview("accept", "")

    async def test_observation_and_verdicts_are_detached_and_precedence_is_owned(self):
        from types import SimpleNamespace
        from llm.components.review import review_action
        observed=[]
        class First(CompletionSession):
            async def review_final(self, value):
                value.response.message.opaque["signature"]="mutated"
                return CompletionReview(ReviewAction.CONTINUE,"fix")
        class Second(CompletionSession):
            async def review_final(self,value):
                observed.append(value.response.message.opaque["signature"])
                return CompletionReview(ReviewAction.BLOCK,"stop")
        original=CompletionReviewObservation(1,(ModelMessage("user","input"),),
            ModelResponse(ModelMessage("assistant","draft",opaque={"signature":"original"})),
            (ModelMessage("user","input"),),())
        context=SimpleNamespace(run=SimpleNamespace(input_message_id="input"))
        async with CompletionPipeline([reviewer(lambda _:Second(),"b"),reviewer(lambda _:First(),"a")],
                                      context,review_policy=POLICY) as pipeline:
            values=await pipeline.review_final(original)
        self.assertEqual([v["name"] for v in values],["a","b"])
        self.assertEqual(review_action(values),ReviewAction.BLOCK)
        self.assertEqual(observed,["original"])
        self.assertEqual(original.response.message.opaque["signature"],"original")
        value=original.to_dict();value["schema_version"]=True
        with self.assertRaises(ValueError):CompletionReviewObservation.from_dict(value)
        for field in ("messages", "original_messages", "tools"):
            value=original.to_dict();value[field]={}
            with self.subTest(field=field),self.assertRaises(ValueError):
                CompletionReviewObservation.from_dict(value)


class CompletionReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.effects=self.root/"effects.txt"

    async def make(self, provider, processors=(), *, review=True, policy=None, usage=None,
                   agent_policy=None, approval=False, extra_components=(), engines=None, completion=None, run_policy=None):
        async def write(arguments):
            with self.effects.open("a") as stream:
                stream.write("effect\n");stream.flush();os.fsync(stream.fileno())
            return "saved"
        tools=RuntimeTools(ToolRegistry((Tool("write","write file",{"type":"object"},write,
            contract=replace(TEST_CONTRACT,approval_required=approval)),)))
        features=[Feature("review_"+p.name,p) for p in processors]
        components=[tools,*features,*extra_components]
        self.provider=provider
        self.app=LargeLanguageModel(self.root/"workspace",components=components,
            engines=engines if engines is not None else {"loop":LoopEngine(completion_adapter=provider)})
        self.addAsyncCleanup(self.app.shutdown)
        self.app.policy_resolver.token_counters["review_test"] = lambda request: 1
        self.events=[]
        self.app.events.subscribe(lambda run,event:self.events.append((run.id,event)))
        config={"parameters":{"engines":{"loop":{"config":{"completion":{"model":"independent-model","output_cap":2}}}},
                              "components":{"tools":{"config":{"enabled":["write"]}}}}}
        if review:config["parameters"]["engines"]["loop"]["policy"]={"final_review":deepcopy(POLICY if policy is None else policy)}
        if completion is not None:config["parameters"]["engines"]["loop"]["config"]["completion"].update(completion)
        if usage is not None:config["policies"]={"usage":{**usage,"counter":"review_test"}}
        if run_policy is not None:config.setdefault("policies",{})["run"]=run_policy
        self.project=await self.app.projects.acreate(config=config,components=[c.name for c in components])
        definition={"engine":"loop","tools":["write"]}
        if agent_policy is not None:definition["policy"]=agent_policy
        await self.project.agents.acreate(definition,identifier="writer")
        self.session=await self.project.sessions.acreate(agent_ids=["writer"])
        return self.session

    async def execute(self,content="original"):
        run,=await (await self.session.run.submit(content,recipients=["writer"])).wait(timeout=15)
        return run

    def public_text(self):
        return [value.text for _,event in self.events for value in (event.delta,event.output)
                if value is not None and value.visibility=="user"]

    async def test_continue_then_accept_preserves_signed_candidate_and_publishes_only_accept(self):
        seen=[]
        class Review(CompletionSession):
            async def after_completion(self,observation):
                seen.append(("after",observation.iteration))
                if False:yield
            async def review_final(self,observation):
                seen.append(("review",observation.iteration))
                return CompletionReview(ReviewAction.CONTINUE if observation.iteration==1 else ReviewAction.ACCEPT,"revise")
            async def finish(self,observation):
                seen.append(("finish",observation.iteration))
                if False:yield
        provider=IndependentAdapter(answer("candidate",seal={"signed":"keep"}),answer("accepted"))
        await self.make(provider,[reviewer(lambda _:Review())])
        run=await self.execute()
        self.assertEqual(run.data.status,RunStatus.COMPLETED,run.data.error)
        self.assertEqual((await run.aresponse()).content,"accepted")
        self.assertFalse(any("candidate" in text for text in self.public_text()))
        self.assertEqual(seen,[("after",1),("review",1),("after",2),("review",2),("finish",2)])
        continuation=next(m for m in provider.requests[1].messages if m.role=="assistant")
        self.assertEqual(continuation.opaque,{"signed":"keep"})
        self.assertIn("reference data",provider.requests[1].messages[-1].text)
        checkpoint=await run.acheckpoint("loop")
        self.assertEqual(checkpoint["records"]["review:1"]["action"],"continue")
        self.assertEqual(checkpoint["records"]["review:2"]["action"],"accept")
        self.assertEqual(len(run.data.metadata["completions"]),2)

    async def test_candidate_remains_private_while_review_waits_and_cancel_closes_it(self):
        entered=asyncio.Event();closed=[]
        class Review(CompletionSession):
            async def review_final(self,value):
                entered.set();await asyncio.Event().wait()
            async def aclose(self,error):closed.append(type(error))
        provider=IndependentAdapter(answer("private"))
        await self.make(provider,[reviewer(lambda _:Review())])
        request=await self.session.run.submit("input",recipients=["writer"])
        await asyncio.wait_for(entered.wait(),10)
        run,=await request.aget_runs()
        self.assertEqual((await run.aresponse()).content,"")
        self.assertEqual(self.public_text(),[])
        await self.session.run.interrupt(run.id)
        run,=await request.wait(timeout=10)
        self.assertEqual(run.data.status,"interrupted")
        self.assertTrue(closed)
        self.assertEqual((await run.aresponse()).content,"")
        self.assertEqual((await run.acheckpoint("loop"))["records"]["review_attempt:1"]["attempt"],1)

    async def test_absent_policy_does_not_activate_registered_reviewers(self):
        called=[]
        class Review(CompletionSession):
            async def review_final(self,value):called.append(value);return CompletionReview(ReviewAction.BLOCK,"")
        await self.make(IndependentAdapter(answer("ordinary")),[reviewer(lambda _:Review())],review=False)
        run=await self.execute()
        self.assertEqual(run.data.status,"completed",run.data.error)
        self.assertEqual(called,[])
        self.assertEqual((await run.aresponse()).content,"ordinary")
        self.assertNotIn("review",(await run.acheckpoint("loop"))["header"])

    async def test_failed_pure_review_resume_reuses_candidate_and_explicit_attempt_budget(self):
        attempts=[]
        class Review(CompletionSession):
            async def review_final(self,value):
                attempts.append(value.iteration)
                if len(attempts)==1:raise ValueError("pure callback failed")
                return CompletionReview(ReviewAction.ACCEPT,"")
        provider=IndependentAdapter(answer("candidate"))
        await self.make(provider,[reviewer(lambda _:Review())])
        failed=await self.execute()
        self.assertEqual(failed.data.status,"failed")
        self.assertEqual((await failed.aresponse()).content,"")
        resumed,=await (await self.session.run.resume(failed.id,agent_id="writer")).wait(timeout=10)
        self.assertEqual(resumed.data.status,"completed",resumed.data.error)
        self.assertEqual(len(provider.requests),1)
        self.assertEqual(attempts,[1,1])
        self.assertEqual((await resumed.acheckpoint("loop"))["records"]["review:1"]["attempt"],2)
        self.assertEqual((await resumed.aresponse()).content,"candidate")

    async def test_block_invalid_exception_timeout_and_oversize_never_publish_or_finish(self):
        for behavior in ('block','invalid','error','timeout','oversize'):
            with self.subTest(behavior=behavior):
                # Separate persisted workspace for each independent failure.
                self.root=self.root/behavior;self.root.mkdir();self.effects=self.root/'effects.txt'
                finished=[]
                class Review(CompletionSession):
                    async def review_final(self,value):
                        if behavior=='invalid':return {'action':'accept'}
                        if behavior=='error':raise RuntimeError('failed review')
                        if behavior=='timeout':await asyncio.Event().wait()
                        return CompletionReview(ReviewAction.BLOCK if behavior=='block' else ReviewAction.ACCEPT,
                                                'x'*257 if behavior=='oversize' else '')
                    async def finish(self,value):
                        finished.append(value)
                        if False:yield
                provider=IndependentAdapter(answer('secret'))
                await self.make(provider,[reviewer(lambda _:Review())],policy={**POLICY,'hook_timeout':0.05})
                run=await self.execute()
                self.assertEqual(run.data.status,'failed')
                self.assertEqual((await run.aresponse()).content,'')
                self.assertEqual(self.public_text(),[])
                self.assertEqual(finished,[])
                self.assertEqual(len(provider.requests),1)
                if behavior=='block':
                    with self.assertRaises(ValueError):await self.session.run.resume(run.id,agent_id='writer')
                await self.app.shutdown()

    async def test_activation_without_implemented_reviewer_fails_before_model(self):
        for processors in ([],[reviewer(lambda _:CompletionSession())]):
            with self.subTest(processors=len(processors)):
                self.root=self.root/str(len(processors));self.root.mkdir()
                provider=IndependentAdapter(answer('unused'))
                await self.make(provider,processors)
                run=await self.execute()
                self.assertEqual(run.data.status,'failed')
                self.assertEqual(provider.requests,[])
                self.assertEqual((await run.aresponse()).content,'')
                await self.app.shutdown()

    async def test_continuation_budget_and_run_call_budget_are_not_reset(self):
        for bounded in ('continuations','calls'):
            with self.subTest(bounded=bounded):
                self.root=self.root/bounded;self.root.mkdir()
                class Review(CompletionSession):
                    async def review_final(self,value):return CompletionReview(ReviewAction.CONTINUE,'again')
                provider=IndependentAdapter(answer('private'))
                await self.make(provider,[reviewer(lambda _:Review())],
                    policy={**POLICY,'max_continuations':0} if bounded=='continuations' else POLICY,
                    usage={'max_calls':1} if bounded=='calls' else None)
                run=await self.execute()
                self.assertEqual(run.data.status,'failed')
                self.assertEqual(run.data.error_code,'completion_review_limit' if bounded=='continuations' else 'usage_limit')
                self.assertEqual(len(provider.requests),1)
                self.assertEqual((await run.aresponse()).content,'')
                if bounded=='continuations':
                    with self.assertRaises(ValueError):await self.session.run.resume(run.id,agent_id='writer')
                await self.app.shutdown()

    async def test_attempt_budget_and_reviewer_revision_cannot_be_reset_by_resume(self):
        class Review(CompletionSession):
            async def review_final(self,value):raise RuntimeError('pure review failed')
        processor=reviewer(lambda _:Review())
        provider=IndependentAdapter(answer('private'))
        await self.make(provider,[processor])
        first=await self.execute()
        processor.review_revision='v2'
        with self.assertRaises(ValueError):await self.session.run.resume(first.id,agent_id='writer')
        processor.review_revision='v1'
        second,=await (await self.session.run.resume(first.id,agent_id='writer')).wait(timeout=10)
        self.assertEqual(second.data.status,'failed')
        self.assertEqual((await second.acheckpoint('loop'))['records']['review_attempt:1']['attempt'],2)
        with self.assertRaises(ValueError):await self.session.run.resume(second.id,agent_id='writer')
        self.assertEqual(len(provider.requests),1)
        self.assertEqual((await second.aresponse()).content,'')

    async def test_missing_or_tampered_review_witness_is_rejected(self):
        from llm.engines.loop.review import LoopReview
        class Review(CompletionSession):
            async def review_final(self,value):return CompletionReview(ReviewAction.ACCEPT,'')
        provider=IndependentAdapter(answer('accepted'))
        await self.make(provider,[reviewer(lambda _:Review())])
        run=await self.execute();checkpoint=await run.acheckpoint('loop')
        mutations=(
            lambda c:c['records']['iteration:1'].pop('review_observation'),
            lambda c:c['records'].pop('review_attempt:1'),
            lambda c:c['records']['review:1'].__setitem__('action','continue'),
            lambda c:c['records']['review:1'].__setitem__('candidate','bad'),
            lambda c:c['records']['review:1']['decisions'][0].__setitem__('revision','other'),
            lambda c:c['records']['review:1'].__setitem__('attempt',True),
            lambda c:c['header'].pop('review'))
        for index,mutate in enumerate(mutations):
            with self.subTest(index=index):
                changed=deepcopy(checkpoint);mutate(changed)
                with self.assertRaises((KeyError,ValueError,TypeError)):LoopReview.require_resume(changed)
        self.assertEqual(len(provider.requests),1)

    async def test_continue_tool_approval_and_resume_execute_real_effect_once(self):
        calls=[]
        class Review(CompletionSession):
            async def review_final(self,value):
                calls.append(value.iteration)
                return CompletionReview(ReviewAction.CONTINUE if value.iteration==1 else ReviewAction.ACCEPT,'check')
        provider=IndependentAdapter(answer('candidate'),
            [{'invoke':[{'key':'write-one','action':'write','values':{}}],'end':'invoke'}],answer('accepted'))
        await self.make(provider,[reviewer(lambda _:Review())],approval=True)
        paused=await self.execute()
        self.assertEqual(paused.data.status,'paused',paused.data.error)
        self.assertFalse(self.effects.exists())
        self.assertEqual((await paused.aresponse()).content,'')
        interaction,=await paused.ainteractions()
        await paused.arespond(interaction.respond('approve'))
        resumed,=await (await self.session.run.resume(paused.id,agent_id='writer')).wait(timeout=15)
        self.assertEqual(resumed.data.status,'completed',resumed.data.error)
        self.assertEqual(self.effects.read_text(),'effect\n')
        self.assertEqual(calls,[1,3])
        self.assertEqual(len(provider.requests),3)
        self.assertEqual((await resumed.aresponse()).content,'accepted')

    async def test_continue_does_not_reset_agent_tool_limit(self):
        class Review(CompletionSession):
            async def review_final(self,value):return CompletionReview(ReviewAction.CONTINUE,'another')
        tool=lambda key:[{'invoke':[{'key':key,'action':'write','values':{}}],'end':'invoke'}]
        provider=IndependentAdapter(tool('first'),answer('candidate'),tool('second'))
        await self.make(provider,[reviewer(lambda _:Review())],agent_policy={'max_tool_calls':1})
        run=await self.execute()
        self.assertEqual(run.data.status,'failed')
        self.assertEqual(self.effects.read_text(),'effect\n')
        self.assertEqual(len(provider.requests),3)
        self.assertEqual((await run.aresponse()).content,'')

    async def test_live_instruction_during_accept_requires_new_reviewed_candidate(self):
        entered=asyncio.Event();release=asyncio.Event();seen=[]
        class Review(CompletionSession):
            async def review_final(self,value):
                seen.append(value.response.message.text)
                if value.iteration==1:entered.set();await release.wait()
                return CompletionReview(ReviewAction.ACCEPT,'')
        provider=IndependentAdapter(answer('obsolete'),answer('corrected'))
        await self.make(provider,[reviewer(lambda _:Review())])
        request=await self.session.run.submit('original',recipients=['writer'])
        await asyncio.wait_for(entered.wait(),10)
        run,=await request.aget_runs()
        await self.session.run.steer(run.id,'correction')
        release.set()
        run,=await request.wait(timeout=10)
        self.assertEqual(run.data.status,'completed',run.data.error)
        self.assertEqual(seen,['obsolete','corrected'])
        self.assertEqual((await run.aresponse()).content,'corrected')
        self.assertFalse(any('obsolete' in text for text in self.public_text()))
        self.assertIn('correction',[m.text for m in provider.requests[1].messages])

    async def test_unknown_usage_reservation_survives_resume_and_blocks_continuation(self):
        calls=[]
        class Review(CompletionSession):
            async def review_final(self,value):
                calls.append(1)
                if len(calls)==1:raise RuntimeError('explicit resume required')
                return CompletionReview(ReviewAction.CONTINUE,'continue')
        provider=IndependentAdapter(answer('candidate',usage=None))
        await self.make(provider,[reviewer(lambda _:Review())],usage={'project_max_tokens':3})
        first=await self.execute()
        self.assertEqual(first.data.status,'failed')
        receipt=first.data.metadata['completions'][0]
        self.assertEqual((receipt['reserved_tokens'],receipt['usage_complete']),(3,False))
        resumed,=await (await self.session.run.resume(first.id,agent_id='writer')).wait(timeout=10)
        self.assertEqual(resumed.data.error_code,'usage_limit')
        self.assertEqual(len(provider.requests),1)
        self.assertEqual((await resumed.aresponse()).content,'')

    async def test_review_observation_and_checkpoint_exclude_invocation_credentials(self):
        class Adapter(IndependentAdapter):
            def describe_config(self):
                value=super().describe_config();value['properties']['credential']={'type':'string'};return value
        observed=[]
        class Review(CompletionSession):
            async def review_final(self,value):observed.append(value.to_dict());return CompletionReview(ReviewAction.ACCEPT,'')
        provider=Adapter(answer('accepted'));secret='private-sdk-test-credential'
        await self.make(provider,[reviewer(lambda _:Review())],completion={'credential':secret})
        run=await self.execute()
        self.assertEqual(run.data.status,'completed',run.data.error)
        self.assertEqual(provider.requests[0].parameters['credential'],secret)
        self.assertNotIn(secret,json.dumps(observed))
        self.assertNotIn(secret,json.dumps(await run.acheckpoint('loop')))
        self.assertNotIn(secret,json.dumps(run.data.metadata))

    async def test_nested_graph_review_resume_reuses_candidate_in_original_scope(self):
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.agent import AgentNode
        seen=[]
        class Review(CompletionSession):
            def __init__(self,context):self.context=context
            async def review_final(self,value):
                seen.append((self.context.run.id,self.context.output_step_id,self.context.checkpoint_scope))
                if len(seen)==1:raise RuntimeError('review again explicitly')
                return CompletionReview(ReviewAction.ACCEPT,'')
        provider=IndependentAdapter(answer('nested accepted'));loop=LoopEngine(completion_adapter=provider)
        await self.make(provider,[reviewer(Review)],extra_components=[WorkflowComponent()],
            engines={'loop':loop,'graph':GraphEngine(handlers={'agent':AgentNode(engines={'loop':loop})})})
        workflow=WorkflowGraph(entry='work').node('work','agent',agent='writer').node('end','end').connect('work','end').to_dict()
        await (await self.project.components.aget('workflows')).acreate(workflow,identifier='flow')
        await self.project.agents.acreate({'engine':'graph','engine_options':{'workflow':'flow'}},identifier='coordinator')
        self.session=await self.project.sessions.acreate(agent_ids=['coordinator'])
        first,=await (await self.session.run.submit('original',recipients=['coordinator'])).wait(timeout=15)
        self.assertEqual(first.data.status,'failed')
        self.assertEqual((await first.aresponse()).content,'')
        run,=await (await self.session.run.resume(first.id,agent_id='coordinator')).wait(timeout=15)
        self.assertEqual(run.data.status,'completed',run.data.error)
        self.assertEqual(len(provider.requests),1)
        self.assertEqual(len(seen),2)
        self.assertTrue(all(step is not None and scope is not None for _,step,scope in seen))
        self.assertEqual(seen[0][0],first.id);self.assertEqual(seen[1][0],run.id)
        checkpoint=await run.acheckpoint('graph')
        receipts=[v for v in checkpoint['records'].values() if v.get('local_key')=='review:1']
        self.assertEqual(len(receipts),1)
        self.assertEqual(len(await self.session.run.alist()),2)

    async def test_parallel_agents_receive_separate_review_sessions_and_candidates(self):
        seen={};entered=asyncio.Event();count=0
        class Review(CompletionSession):
            def __init__(self,context):self.context=context;self.observed=[]
            async def review_final(self,value):
                nonlocal count
                self.observed.append(value.response.message.text);count+=1
                if count==2:entered.set()
                await asyncio.wait_for(entered.wait(),10)
                seen[self.context.run.agent_id]=(self.context.run.id,list(self.observed))
                return CompletionReview(ReviewAction.ACCEPT,'')
        provider=IndependentAdapter(answer('first'),answer('second'))
        await self.make(provider,[reviewer(Review)])
        await self.project.agents.acreate({'engine':'loop','tools':['write']},identifier='other')
        self.session=await self.project.sessions.acreate(agent_ids=['other','writer'])
        runs=await (await self.session.run.submit('shared input',recipients='all')).wait(timeout=15)
        self.assertEqual([r.data.status for r in runs],['completed','completed'])
        self.assertEqual(set(seen),{'writer','other'})
        self.assertEqual({tuple(v[1]) for v in seen.values()},{('first',),('second',)})
        self.assertEqual({v[0] for v in seen.values()},{r.id for r in runs})

    async def test_memory_goal_processing_preserves_original_tool_exchange_during_review(self):
        from llm.components.memory import MemoryComponent
        from llm.components.goals import GoalComponent
        from tests.llm.configuration_fixtures import memory_settings
        from tests.llm.test_goals import goal
        observed=[]
        class Review(CompletionSession):
            async def review_final(self,value):
                observed.append(value)
                return CompletionReview(ReviewAction.CONTINUE if value.iteration==2 else ReviewAction.ACCEPT,'check')
        provider=IndependentAdapter([{'invoke':[{'key':'write-once','action':'write','values':{}}],'end':'invoke'}],
                                    answer('candidate'),answer('accepted'))
        await self.make(provider,[reviewer(lambda _:Review())],extra_components=[MemoryComponent(),GoalComponent()])
        memory=await self.project.components.aget('memory');goals=await self.project.components.aget('goals')
        await memory.aconfigure(memory_settings({'processing':{'priority':100,'compress_tools':True,'tool_result_chars':20}}))
        await goals.acreate(goal(),identifier='goal')
        await goals.aconfigure({'config':{'priority':10},'policy':{'inject':True}})
        before=await goals.asnapshot('goal')
        run=await self.execute()
        self.assertEqual(run.data.status,'completed',run.data.error)
        self.assertEqual(self.effects.read_text(),'effect\n')
        self.assertEqual([v.iteration for v in observed],[2,3])
        self.assertEqual((await run.aresponse()).content,'accepted')
        self.assertEqual(await goals.asnapshot('goal'),before)
        for request in provider.requests:request.validate_exchanges()
        self.assertEqual([m.result.call_id for m in provider.requests[-1].messages if m.result is not None],['write-once'])

    async def test_parent_review_budgets_cannot_be_disabled_or_widened(self):
        from llm.core.models import ProjectConfig
        from jsonschema.exceptions import ValidationError
        engine=LoopEngine(completion_adapter=IndependentAdapter())
        parent=ProjectConfig(parameters={'engines':{'loop':{'policy':{'final_review':POLICY}}}})
        for policy in (None,{**POLICY,'max_attempts':3},{**POLICY,'hook_timeout':6}):
            with self.subTest(policy=policy):
                child=engine.for_agent({'engine':'loop','engine_options':{'policy':{'final_review':policy}}})
                with self.assertRaises(ValueError):child.resolve_config(parent,'loop')
        narrow={**POLICY,'max_continuations':1}
        child=engine.for_agent({'engine':'loop','engine_options':{'policy':{'final_review':narrow}}})
        self.assertEqual(child.resolve_config(parent,'loop')['values']['policy']['final_review'],narrow)
        for key in POLICY:
            partial={k:v for k,v in POLICY.items() if k!=key}
            with self.subTest(missing=key),self.assertRaises(ValidationError):
                engine.for_agent({'engine':'loop','engine_options':{'policy':{'final_review':partial}}})

    async def test_run_timeout_cancels_review_without_publishing_candidate(self):
        entered=[];closed=[]
        class Review(CompletionSession):
            async def review_final(self,value):entered.append(1);await asyncio.Event().wait()
            async def aclose(self,error):closed.append(type(error))
        provider=IndependentAdapter(answer('private'))
        await self.make(provider,[reviewer(lambda _:Review())],run_policy={'timeout_seconds':1})
        run=await self.execute()
        self.assertEqual(run.data.error_code,'run_timeout')
        self.assertEqual(entered,[1]);self.assertTrue(closed)
        self.assertEqual((await run.aresponse()).content,'')
        self.assertEqual(self.public_text(),[])
        self.assertEqual(len(provider.requests),1)

    async def test_same_run_token_budget_remains_active_after_continue(self):
        class Review(CompletionSession):
            async def review_final(self,value):return CompletionReview(ReviewAction.CONTINUE,'again')
        provider=IndependentAdapter(answer('candidate',usage=2))
        await self.make(provider,[reviewer(lambda _:Review())],usage={'max_tokens':3})
        run=await self.execute()
        self.assertEqual(run.data.error_code,'usage_limit')
        self.assertEqual(len(provider.requests),1)
        self.assertEqual((await run.aresponse()).content,'')

    async def test_verdict_write_failure_rolls_back_and_resume_keeps_original_effect(self):
        import llm.services.runtime.checkpoints as checkpoint_storage
        reviews=[]
        class Review(CompletionSession):
            async def review_final(self,value):reviews.append(value.iteration);return CompletionReview(ReviewAction.ACCEPT,'')
        provider=IndependentAdapter([{'invoke':[{'key':'write-once','action':'write','values':{}}],'end':'invoke'}],answer('accepted'))
        await self.make(provider,[reviewer(lambda _:Review())])
        original=checkpoint_storage.atomic_json;failed=[]
        def write_then_fail(path,value):
            original(path,value)
            if value.get('key')=='review:2':
                failed.append(str(path));raise OSError('injected verdict write failure')
        with patch.object(checkpoint_storage,'atomic_json',side_effect=write_then_fail):
            first=await self.execute()
        self.assertEqual(first.data.status,'failed')
        self.assertEqual(len(failed),1)
        self.assertEqual((await first.aresponse()).content,'')
        checkpoint=await first.acheckpoint('loop')
        self.assertNotIn('review:2',checkpoint['records'])
        self.assertEqual(checkpoint['records']['review_attempt:2']['attempt'],1)
        self.assertEqual(checkpoint['records']['iteration:2']['response']['message']['content'],'accepted')
        self.assertEqual(self.effects.read_text(),'effect\n')
        run,=await (await self.session.run.resume(first.id,agent_id='writer')).wait(timeout=15)
        self.assertEqual(run.data.status,'completed',run.data.error)
        self.assertEqual((await run.aresponse()).content,'accepted')
        self.assertEqual(len(provider.requests),2)
        self.assertEqual(reviews,[2,2])
        self.assertEqual(self.effects.read_text(),'effect\n')

    async def test_processor_cannot_publish_root_candidate_before_verdict(self):
        from llm.engines.base import EngineEvent,EngineEventType,EngineOutput
        class Review(CompletionSession):
            async def after_completion(self,value):yield EngineEvent(EngineEventType.OUTPUT,output=EngineOutput(text='leak'))
            async def review_final(self,value):raise AssertionError('must not reach reviewer')
        provider=IndependentAdapter(answer('private'))
        await self.make(provider,[reviewer(lambda _:Review())])
        run=await self.execute()
        self.assertEqual(run.data.status,'failed')
        self.assertEqual((await run.aresponse()).content,'')
        self.assertEqual(self.public_text(),[])
