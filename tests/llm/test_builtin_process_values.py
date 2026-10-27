"""Process request presence and response wire remain independent of runtime fields."""

from copy import deepcopy
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from jsonschema import Draft202012Validator

from llm.components.tools.builtin.process_values import (ProcessStartArguments, ShellArguments, CheckArguments,
    ProcessLimits, ProcessStatus, ProcessInvocation)
from llm.components.tools.builtin.processes import ProcessSession, OutputPage, ProcessTools
from llm.components.tools.builtin.files import FileTools
from llm.services.infrastructure.process_environment import ProcessEnvironment
from llm.core.configuration import UNSET
from llm.services.infrastructure.captured_text import CapturedText


class BuiltinProcessValuesTests(unittest.TestCase):
    def test_environment_omission_empty_and_explicit_null_are_distinct(self):
        raw={"argv": ["/bin/true"]};original=deepcopy(raw)
        value=ProcessStartArguments.read(raw)
        self.assertEqual(value.environment(), {})
        self.assertEqual(value.directory, ".")
        self.assertFalse(value.stdin)
        self.assertEqual(raw, original)
        self.assertEqual(ProcessInvocation.with_environment(raw,{"A":"captured"}),
            {"argv":["/bin/true"],"env":{"A":"captured"}})
        self.assertEqual(ProcessInvocation.with_environment({**raw,"env":{}},{"A":"captured"}),
            {"argv":["/bin/true"],"env":{}})
        self.assertEqual(ProcessStartArguments.read({**raw,"env":{}}).environment(), {"env":{}})
        with self.assertRaisesRegex(ValueError, "mapping of strings"):
            ProcessStartArguments.read({**raw,"env":None}).environment()
        schema=ProcessEnvironment.schema();validator=Draft202012Validator(schema)
        for env in ({}, {"A":"", "B":"value"}, {"line\nbreak":"allowed\nvalue"}):
            self.assertEqual(ProcessEnvironment.copy(env),env)
            self.assertTrue(validator.is_valid(env))
        for env in ({"":"value"},{"A=B":"x"},{"A\0":"x"},{"A":"x\0"},{"A":1}):
            with self.subTest(env=env),self.assertRaises(ValueError):ProcessEnvironment.copy(env)
            self.assertFalse(validator.is_valid(env))
        schema["additionalProperties"]["type"]="integer"
        self.assertEqual(ProcessEnvironment.schema()["additionalProperties"]["type"],"string")

    def test_shell_and_named_check_keep_only_original_explicit_overrides(self):
        raw={"command":"echo value", "env":{}, "timeout_seconds":None};original=deepcopy(raw)
        args=ShellArguments.read(raw).invocation(["/bin/sh","-c"]).supplied()
        self.assertEqual(args,{"argv":["/bin/sh","-c","echo value"],"env":{},"timeout_seconds":None})
        self.assertEqual(raw,original)
        check=CheckArguments.read({"name":"unit"})
        self.assertEqual(check.invocation({"unit":["/bin/true"]}).supplied(),{"argv":["/bin/true"]})
        self.assertEqual(CheckArguments.choices_schema({"unit":[]})["properties"]["name"],{"enum":["unit"]})
        self.assertEqual(ProcessStartArguments.schema()["required"],["argv"])
        for invalid in (True,0,-1,float("inf")):
            with self.subTest(timeout=invalid),self.assertRaisesRegex(ValueError,"positive and finite"):
                ProcessLimits(timeout_seconds=invalid).validate_limits()
        ProcessLimits(timeout_seconds=None,max_output_bytes=None).validate_limits()
        with self.assertRaisesRegex(ValueError,"positive integer"):
            ProcessLimits(max_output_bytes=True).validate_limits()

    def test_session_snapshot_preserves_exact_wire_and_detaches_argv(self):
        process=SimpleNamespace(pid=77,returncode=None,stdin=None)
        session=ProcessSession(process,["/bin/echo","text"],"/work","start")
        self.addCleanup(session.stdout.close);self.addCleanup(session.stderr.close)
        session.stdout.extend(b"ok\n");session.stderr.extend(b"\xff")
        snapshot=session.snapshot("id")
        self.assertEqual(snapshot,{"process_id":"id","status":"running","returncode":None,"pid":77,
            "argv":["/bin/echo","text"],"cwd":"/work","started_at":"start","ended_at":None,
            "stdin_open":False,"stdout":"ok\n","stderr":"\ufffd","truncated":False})
        snapshot["argv"].clear()
        self.assertEqual(session.argv,["/bin/echo","text"])
        session.status=ProcessStatus.CANCELLED;session.ended_at="end"
        self.assertEqual(session.snapshot("id")["status"],"cancelled")

    def test_output_cursor_does_not_consume_partial_utf8_or_rewrite_explicit_count(self):
        data="한".encode()
        buffer=CapturedText();self.addCleanup(buffer.close)
        buffer.extend(data[:1])
        waiting=OutputPage.read(buffer,0,UNSET,final=False)
        self.assertEqual((waiting.text,waiting.next_offset,waiting.remaining),("",0,0))
        buffer.extend(data[1:])
        complete=OutputPage.read(buffer,0,UNSET,final=False)
        self.assertEqual((complete.text,complete.next_offset,complete.remaining),("한",1,0))
        partial=CapturedText();self.addCleanup(partial.close)
        partial.extend(data[:1]);partial.finish()
        truncated=OutputPage.read(partial,0,UNSET,final=True)
        self.assertEqual((truncated.text,truncated.next_offset),("\ufffd",1))
        for count in (None,True,0,-1):
            with self.subTest(count=count),self.assertRaisesRegex(ValueError,"positive integer"):
                OutputPage.read(buffer,0,count,final=True)
        for offset in (True,-1,2):
            with self.subTest(offset=offset),self.assertRaisesRegex(ValueError,"outside"):
                OutputPage.read(buffer,offset,UNSET,final=True)


class ProcessExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_keeps_public_start_and_cancel_hooks_with_real_effects(self):
        started=asyncio.Event();calls=[]
        class SelectedEnvironment(ProcessTools):
            async def start(self, args):
                calls.append("start")
                result=await super().start({"env":{"SELECTED":"captured"},**args})
                started.set()
                return result

            async def cancel(self, args):
                calls.append("cancel")
                return await super().cancel(args)

        with TemporaryDirectory() as root:
            tools=SelectedEnvironment(FileTools(Path(root)))
            pending=asyncio.create_task(tools.execute({"argv":[sys.executable,"-I","-c",
                "import os,sys; print(os.environ['SELECTED'],flush=True); sys.stdin.read()"],"stdin":True}))
            try:
                await asyncio.wait_for(started.wait(),5)
                session,=tools.sessions.values()
                async def observed():
                    while not session.stdout:await asyncio.sleep(.005)
                await asyncio.wait_for(observed(),5)
                identifier=next(iter(tools.sessions))
                self.assertEqual((await tools.output({"process_id":identifier,"max_chars":100}))["stdout"],"captured\n")
                self.assertIsNone(session.process.returncode)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):await pending
                self.assertEqual(calls,["start","cancel"])
                self.assertIsNotNone(session.process.returncode)
            finally:
                pending.cancel()
                await asyncio.gather(pending,return_exceptions=True)
                await tools.close()
