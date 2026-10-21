"""Missing operation evidence never grants another external effect."""

import json
import asyncio
from copy import deepcopy
import shutil
import sys
import textwrap
import unittest
from unittest.mock import patch

from llm.services.runtime import operations
from llm.services.runtime.operations import OperationRecord
from llm.services.runtime.tools import ToolExecutionError, ToolRuntime
from tests.llm import test_isolated_tools as fixture


class OperationIntegrityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.OperationTests.asyncSetUp
    open = fixture.OperationTests.open
    execute = fixture.OperationTests.execute

    def file_effect(self, *, fail_first=False):
        path = self.root / 'effect-proof.txt'
        attempts = []
        async def effect(tool, call):
            attempts.append(call.step_id)
            if fail_first and len(attempts) == 1:
                raise ToolExecutionError('not applied', effect='none')
            with path.open('a') as stream:
                stream.write('effect\n')
            return None
        self.app.services.tool_runtime = ToolRuntime(
            operation_key=lambda call: call.arguments['operation'], runner=effect)
        return path, attempts

    async def reopen_and_try(self):
        try:
            self.app = self.open()
            self.session = await (await self.app.projects.aload(self.project.id)).sessions.aload(self.session.id)
            return str((await self.execute()).data.status)
        except (ValueError, OSError):
            return 'rejected'

    async def test_missing_completed_ledger_cannot_repeat_file_effect(self):
        effect, attempts = self.file_effect()
        self.assertEqual((await self.execute()).data.status, 'completed')
        receipt = await self.session.run.aoperation('invoice-1')
        path = self.session.data.paths.state / 'tool_operations' / (receipt['idempotency_key'] + '.json')
        await self.app.shutdown()
        path.unlink()
        status = await self.reopen_and_try()
        self.assertEqual((len(attempts), effect.read_text(), path.exists()), (1, 'effect\n', False))
        self.assertNotEqual(status, 'completed')

    async def test_missing_no_effect_evidence_cannot_allow_new_effect(self):
        effect, attempts = self.file_effect(fail_first=True)
        await self.execute()
        receipt = await self.session.run.aoperation('invoice-1')
        self.assertEqual(receipt['status'], 'not_applied')
        path = self.session.data.paths.state / 'tool_operations' / (receipt['idempotency_key'] + '.json')
        await self.app.shutdown()
        receipt.pop('evidence')
        path.write_text(json.dumps(receipt))
        damaged = path.read_bytes()
        status = await self.reopen_and_try()
        self.assertEqual((len(attempts), effect.exists(), path.read_bytes() == damaged), (1, False, True))
        self.assertNotEqual(status, 'completed')

    async def lose_ledger(self, *, whole_root):
        effect, attempts = self.file_effect()
        await self.execute()
        root = self.session.data.paths.state / 'tool_operations'
        await self.app.shutdown()
        if whole_root:
            shutil.rmtree(root)
        else:
            (root / 'manifest.json').unlink()
        status = await self.reopen_and_try()
        self.assertEqual((len(attempts), effect.read_text()), (1, 'effect\n'))
        self.assertFalse((root / 'manifest.json').exists())
        self.assertNotEqual(status, 'completed')

    async def test_missing_manifest_cannot_be_bootstrapped_on_reopen(self):
        await self.lose_ledger(whole_root=False)

    async def test_missing_ledger_root_cannot_be_bootstrapped_on_reopen(self):
        await self.lose_ledger(whole_root=True)

    async def test_record_fields_and_state_evidence_are_required(self):
        self.file_effect()
        await self.execute()
        value = await self.session.run.aoperation('invoice-1')
        self.assertIsNone(value['result'])
        for field in value:
            with self.subTest(field=field), self.assertRaises(ValueError):
                OperationRecord.from_dict({key: item for key, item in value.items() if key != field})
        changes = [
            {'evidence': None}, {'ended_at': None}, {'evidence': ''},
            {'request_digest': '0' * 64}, {'arguments': []}, {'attempts': None},
            {'observations': {}}, {'status': 'started'}, {'created_at': '2026-01-01'},
            {'schema_version': True}, {'schema_version': 1.0},
            {'attempts': [{'run_id': 'r', 'step_id': 's', 'status': 'not_applied',
                           'created_at': value['created_at'], 'ended_at': value['created_at']}]},
            {'observations': [{'status': 'not_applied', 'at': value['created_at']}]},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                OperationRecord.from_dict({**deepcopy(value), **change})

    async def test_completion_manifest_failure_rolls_back_receipt_without_repeating_effect(self):
        effect, attempts = self.file_effect()
        original = operations.atomic_json
        writing_completion = False
        def fail_manifest(path, value):
            nonlocal writing_completion
            if value.get('status') == 'completed':
                writing_completion = True
            if writing_completion and path.name == 'manifest.json':
                raise OSError('manifest write failed')
            return original(path, value)
        with patch.object(operations, 'atomic_json', fail_manifest):
            failed = await self.execute()
        self.assertEqual(failed.data.status, 'failed')
        self.assertEqual((await self.session.run.aoperation('invoice-1'))['status'], 'started')
        await self.app.shutdown()
        status = await self.reopen_and_try()
        self.assertNotEqual(status, 'completed')
        self.assertEqual((len(attempts), effect.read_text()), (1, 'effect\n'))

    async def test_process_death_between_record_and_manifest_preserves_unclaimed_state(self):
        effect, attempts = self.file_effect()
        project_id, session_id = self.project.id, self.session.id
        await self.app.shutdown()
        code = textwrap.dedent('''
            import asyncio, os, sys
            from pathlib import Path
            from llm.llm import LargeLanguageModel, BackendServices
            from llm.components.tools import Tool, ToolRegistry
            from llm.services.runtime import operations
            from llm.services.runtime.tools import ToolRuntime
            from tests.llm.test_isolated_tools import ToolEngine
            from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
            root = Path(sys.argv[1])
            original = operations.atomic_json
            def crash(path, value):
                original(path, value)
                if value.get('status') == 'started':
                    os._exit(77)
            operations.atomic_json = crash
            async def effect(arguments):
                (root / 'child-effect.txt').write_text('unexpected effect')
            async def main():
                tools = RuntimeTools(ToolRegistry([Tool('external', 'effect', {'type': 'object'}, effect, contract=TEST_CONTRACT)]))
                async with LargeLanguageModel(root, components=[tools], engines={'tool': ToolEngine()},
                        services=BackendServices(tool_runtime=ToolRuntime(operation_key=lambda call: call.arguments['operation']))) as app:
                    session = await (await app.projects.aload(sys.argv[2])).sessions.aload(sys.argv[3])
                    await (await session.run.submit('effect', engine='tool')).wait()
            asyncio.run(main())
        ''')
        child = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(self.root), project_id, session_id,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, error = await asyncio.wait_for(child.communicate(), 15)
        self.assertEqual(child.returncode, 77, error.decode(errors='replace'))
        self.assertFalse((self.root / 'child-effect.txt').exists())
        status = await self.reopen_and_try()
        self.assertEqual(status, 'completed')
        self.assertEqual((len(attempts), effect.read_text()), (1, 'effect\n'))
