"""Real process termination after durable review evidence or publication."""
import asyncio
import json
import os
from pathlib import Path
import sys

from llm.components.processing import CompletionSession
from llm.components.review import CompletionReview, ReviewAction
from llm.components.tools import Tool, ToolRegistry
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_common_provider_acceptance import IndependentAdapter
from tests.llm.test_completion_processing import Feature
from tests.llm.test_completion_review import POLICY, answer, reviewer


def append(root,name,value):
    with (root/(name+'.jsonl')).open('a') as stream:
        stream.write(json.dumps(value)+'\n');stream.flush();os.fsync(stream.fileno())


def rows(root,name):
    path=root/(name+'.jsonl')
    return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []


def marker(root,**value):
    path=root/'case.json';previous=json.loads(path.read_text()) if path.exists() else {}
    with path.open('w') as stream:
        json.dump({**previous,**value},stream);stream.flush();os.fsync(stream.fileno())


class RecordedAdapter(IndependentAdapter):
    def __init__(self,root,*scripts):super().__init__(*scripts);self.root=root
    async def stream(self,request,**kwargs):
        append(self.root,'models',{'call':len(rows(self.root,'models'))+1})
        async for value in super().stream(request,**kwargs):yield value


def backend(root,boundary,*,recovery=False):
    def stop(run_id):
        marker(root,reached=boundary,run_id=run_id);os._exit(94)
    class Review(CompletionSession):
        def __init__(self,context):self.run_id=context.run.id
        async def review_final(self,value):
            action=ReviewAction.CONTINUE if value.response.message.text=='candidate' else ReviewAction.ACCEPT
            append(root,'reviews',{'iteration':value.iteration,'action':str(action)})
            if not recovery and boundary=='computed' and action==ReviewAction.CONTINUE:stop(self.run_id)
            return CompletionReview(action,'inspect again')
    async def effect(arguments):
        append(root,'effects',{'effect':'once'})
        return 'written'
    tools=RuntimeTools(ToolRegistry((Tool('write','write file',{'type':'object'},effect,contract=TEST_CONTRACT),)))
    feature=Feature('review_test',reviewer(Review))
    scripts=[answer('accepted')] if recovery else [
        [{'invoke':[{'key':'write-once','action':'write','values':{}}],'end':'invoke'}],
        answer('candidate'),answer('accepted')]
    adapter=RecordedAdapter(root,*scripts)
    app=LargeLanguageModel(root/'workspace',components=[tools,feature],engines={'loop':LoopEngine(completion_adapter=adapter)})
    def observe(run,event):
        if recovery:return
        wanted={'responded':'iteration:2','attempt':'review_attempt:2','continue':'review:2','accept':'review:3'}.get(boundary)
        if event.type=='checkpoint' and wanted is not None and event.metadata.get('key')==wanted:stop(run.id)
        if boundary=='published' and event.type=='output' and event.output is not None and event.output.step_id is None:stop(run.id)
    app.events.subscribe(observe,channel='engine')
    return app,adapter


async def child(root,boundary):
    root.mkdir(parents=True)
    app,_=backend(root,boundary)
    async with app:
        project=await app.projects.acreate(components=['tools','review_test'],config={
            'parameters':{'engines':{'loop':{'config':{'completion':{'model':'independent-model','output_cap':2}},
                                           'policy':{'final_review':POLICY}}},
                          'components':{'tools':{'config':{'enabled':['write']}}}}})
        await project.agents.acreate({'engine':'loop','tools':['write']},identifier='writer')
        session=await project.sessions.acreate(agent_ids=['writer'])
        marker(root,project_id=project.id,session_id=session.id)
        run,=await (await session.run.submit('write once then answer',recipients=['writer'])).wait(timeout=15)
        raise AssertionError('Unreached crash boundary '+boundary+': '+str(run.data.error))


if __name__=='__main__':asyncio.run(child(Path(sys.argv[1]),sys.argv[2]))
