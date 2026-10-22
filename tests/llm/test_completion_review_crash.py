"""Abrupt process loss preserves review decisions and real Tool receipts."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tests.llm.support.review_crash_worker import backend,rows


class ReviewCrashTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,boundary):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'case'
            result=await asyncio.to_thread(subprocess.run,
                [sys.executable,'-c',
                 'import asyncio,sys; from pathlib import Path; '
                 'from tests.llm.support.review_crash_worker import child; '
                 'asyncio.run(child(Path(sys.argv[1]),sys.argv[2]))',str(root),boundary],
                capture_output=True,text=True,timeout=40,cwd=Path(__file__).parents[2])
            self.assertEqual(result.returncode,94,result.stdout+result.stderr)
            evidence=json.loads((root/'case.json').read_text())
            self.assertEqual(evidence['reached'],boundary)
            self.assertEqual(rows(root,'effects'),[{'effect':'once'}])
            model_count=len(rows(root,'models'));review_count=len(rows(root,'reviews'))
            app,adapter=backend(root,boundary,recovery=True)
            async with app:
                project=await app.projects.aload(evidence['project_id'])
                session=await project.sessions.aload(evidence['session_id'])
                await session.run.start();await asyncio.wait_for(session.run.wait_idle(),10)
                source,=await session.run.alist()
                self.assertEqual(source.id,evidence['run_id'])
                self.assertEqual(source.data.status,'interrupted')
                self.assertEqual((await source.aresponse()).content,'accepted' if boundary=='published' else '')
                self.assertEqual(len(adapter.requests),0)
                self.assertEqual(len(rows(root,'reviews')),review_count)
                run,=await (await session.run.resume(source.id,agent_id='writer')).wait(timeout=15)
                self.assertEqual(run.data.status,'completed',run.data.error)
                self.assertEqual((await run.aresponse()).content,'accepted')
                self.assertEqual(rows(root,'effects'),[{'effect':'once'}])
                self.assertEqual(len(rows(root,'models'))-model_count,0 if boundary in ('accept','published') else 1)
                checkpoint=await run.acheckpoint('loop')
                self.assertEqual(checkpoint['records']['review:2']['action'],'continue')
                self.assertEqual(checkpoint['records']['review:3']['action'],'accept')
                self.assertEqual(checkpoint['records']['review_attempt:2']['attempt'],2 if boundary in ('attempt','computed') else 1)

    async def test_response_and_attempt_ack_process_loss(self):
        for boundary in ('responded','attempt','computed'):
            with self.subTest(boundary=boundary):await self.exercise(boundary)

    async def test_decision_and_publication_process_loss(self):
        for boundary in ('continue','accept','published'):
            with self.subTest(boundary=boundary):await self.exercise(boundary)
