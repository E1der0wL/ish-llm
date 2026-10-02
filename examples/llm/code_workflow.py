"""등록된 Agent와 Workflow로 코드 작성/정적 검사/테스트/재수정을 실행하는 예제.

기본값은 재현 가능한 모델 응답이며 파일 작성과 Python 검증은 실제 수행한다.
--model을 지정하면 같은 경로에서 LiteLLM completion을 호출한다.
"""

import argparse
import ast
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from asyncio import timeout
from contextlib import aclosing
from llm.components.workflows import WorkflowGraph
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.engines.graph.agent import AgentNode
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.llm import LargeLanguageModel
from llm.providers.litellm import completion


def coding_workflow(max_attempts: int = 3) -> dict:
    """정적 검사 실패는 테스트를 건너뛰고 다음 구현 회차로 돌아간다."""
    body = (WorkflowGraph(entry="implement")
            .node("implement", "agent", agent="coder", output_format="json",
                  inputs={"request": "/request", "previous_code": "/code", "validation": "/validation"},
                  outputs={"code": "/data/code"},
                  output_schema={"type": "object", "required": ["data"], "properties": {
                      "data": {"type": "object", "required": ["code"], "properties": {
                          "code": {"type": "string", "maxLength": 10000}}}}})
            .node("persist", "persist_code")
            .node("static", "static_check")
            .node("route", "branch", cases=[{
                "port": "valid", "when": {"path": "/static_ok", "op": "eq", "value": True},
            }], default="invalid")
            .node("tests", "test_code", timeout_seconds=15)
            .node("round_end", "end")
            .connect("implement", "persist").connect("persist", "static").connect("static", "route")
            .connect("route", "tests", port="valid").connect("route", "round_end", port="invalid")
            .connect("tests", "round_end").to_dict())
    return (WorkflowGraph(entry="repair", inputs={"request": "/prompt"},
                          initial_state={"passed": False, "attempt": 0, "history": [], "code": "", "validation": None})
            .node("repair", "loop", body=body, max_iterations=max_attempts, on_limit="fail",
                  **{"while": {"path": "/passed", "op": "eq", "value": False}})
            .node("report", "report").node("end", "end")
            .connect("repair", "report").connect("report", "end").to_dict())


class DemonstrationCompletion:
    """고정 응답: 문법 오류 → 의미 오류 → 올바른 구현. 실제 모델 테스트와 명확히 구분한다."""

    def __init__(self):
        self.requests = []

    def __call__(self, **kwargs):
        self.requests.append(kwargs)
        sources = ["def add(a, b)\n    return a + b\n",
                   "def add(a, b):\n    return a - b\n",
                   "def add(a, b):\n    return a + b\n"]
        text = json.dumps({"code": sources[min(len(self.requests) - 1, 2)]})
        for offset in range(0, len(text), 16):
            yield {"choices": [{"index": 0, "delta": {"content": text[offset:offset + 16]},
                                "finish_reason": None}]}
        yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}


def inspect_source(code: str) -> list[str]:
    """예제 계약: 외부 접근/호출 없이 산술식으로 add(a,b)를 구현하는 작은 순수 함수."""
    try:
        tree = ast.parse(code)
        compile(tree, "solution.py", "exec")
    except (SyntaxError, ValueError) as error:
        return [str(error)]
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return ["Exactly one function add(a, b) is required"]
    fn = tree.body[0]
    if (fn.name != "add" or [arg.arg for arg in fn.args.args] != ["a", "b"] or fn.decorator_list
            or fn.args.defaults or fn.args.kw_defaults or fn.args.posonlyargs or fn.args.kwonlyargs
            or fn.args.vararg or fn.args.kwarg or fn.returns or any(arg.annotation for arg in fn.args.args)):
        return ["Use an undecorated add(a, b) with no defaults or annotations"]
    allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.BinOp,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.UnaryOp,
               ast.Name, ast.Load, ast.Constant)
    if any(not isinstance(item, allowed) for item in ast.walk(tree)):
        return ["Only a pure arithmetic expression is supported by this demonstration"]
    if any(isinstance(item, ast.Name) and item.id not in ("a", "b") for item in ast.walk(tree)):
        return ["Only arguments a and b may be referenced"]
    if any(isinstance(item, ast.Constant) and (type(item.value) not in (int, float)
                                              or abs(item.value) > 1_000_000) for item in ast.walk(tree)):
        return ["Only small numeric constants are supported"]
    return []


class CodeChecks:
    """실제 AST 검사와 별도 Python 프로세스의 unittest를 실행한다."""

    def __init__(self, output: Path):
        self.output = output

    async def write(self, node):
        """예제 파일 저장은 모델 실행과 분리한다. 파일 경로는 개발자가 지정한다."""
        await asyncio.to_thread(self.output.write_text, node.state["code"], encoding="utf-8")
        return {"attempt": node.state["attempt"] + 1, "passed": False}

    async def static(self, node):
        errors = await asyncio.to_thread(inspect_source, node.state["code"])
        validation = {"phase": "static", "ok": not errors, "errors": errors}
        history = node.state["history"] + [{"attempt": node.state["attempt"], **validation}]
        return {"static_ok": not errors, "passed": False, "validation": validation, "history": history}

    async def tests(self, node):
        # 생성 코드가 검사 이후 바뀌었으면 테스트하지 않는다.
        source = await asyncio.to_thread(self.output.read_text, encoding="utf-8")
        if source != node.state["code"] or inspect_source(source):
            raise ValueError("Generated source changed or did not pass static validation")
        script = '''
import importlib.util, sys, unittest
spec = importlib.util.spec_from_file_location("solution", sys.argv[1])
solution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solution)
class AdditionTests(unittest.TestCase):
    def test_positive(self): self.assertEqual(solution.add(2, 3), 5)
    def test_negative(self): self.assertEqual(solution.add(-4, -6), -10)
    def test_zero(self): self.assertEqual(solution.add(0, 0), 0)
    def test_float(self): self.assertAlmostEqual(solution.add(0.1, 0.2), 0.3)
    def test_commutative(self): self.assertEqual(solution.add(7, -2), solution.add(-2, 7))
sys.argv = [sys.argv[0]]
unittest.main(verbosity=2)
'''
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", "-c", script, str(self.output),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with timeout(10):
                stdout, stderr = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
        details = (stdout + stderr).decode("utf-8", errors="replace")
        validation = {"phase": "tests", "ok": process.returncode == 0, "output": details}
        return {"passed": validation["ok"], "validation": validation,
                "history": node.state["history"] + [{"attempt": node.state["attempt"], **validation}]}


async def report(node):
    async def text(context):
        yield f"Completed after {node.state['attempt']} implementation attempts.\n"
    async with aclosing(BaseEngine().step(node.context, text, name="Report", kind="text")) as events:
        async for event in events:
            await node.emit(event)
    return {}


async def run_demo(workspace: Path, *, model=None, completion_fn=None, max_attempts=3, display=True):
    """정의를 등록하고 디스크에서 다시 로드한 Workflow를 RunManager로 실행한다."""
    workspace = workspace.resolve()
    # 매번 고유한 작업 디렉토리를 만든다. 사용자 저장소나 이전 결과를 덮어쓰지 않는다.
    from uuid import uuid4
    workdir = workspace / "coding-artifacts" / uuid4().hex
    workdir.mkdir(parents=True)
    output = workdir / "solution.py"
    fn = completion_fn or (completion if model else DemonstrationCompletion())
    agent, checks = AgentNode(engines={"loop": LoopEngine(completion_fn=fn)}), CodeChecks(output)
    engine = GraphEngine("code-review", handlers={"agent": agent, "persist_code": checks.write, "static_check": checks.static,
                                                  "test_code": checks.tests, "report": report})

    def observe(run, event):
        if not display:
            return
        if event.type == EngineEventType.STEP_STARTED and event.kind == "graph_node":
            print("Node:", event.metadata["path"], flush=True)
        elif event.type == EngineEventType.TEXT_DELTA and event.delta.visibility == "user":
            print(event.delta.text, end="", flush=True)

    async with LargeLanguageModel(workspace, engines={"graph": engine}, on_event=observe) as backend:
        project = await backend.projects.acreate("Code repair workflow", components=["agents", "workflows"])
        agents = await project.components.aget("agents")
        workflows = await project.components.aget("workflows")
        await agents.acreate({"engine": "loop", "purpose": "Implement and repair a pure addition function",
                             "system_prompt": "Return only JSON with a code string. Implement add(a, b) using a pure arithmetic expression. No imports, calls, annotations, decorators, defaults or docstrings. Fix the reported validation errors.",
                             "completion": {"model": model or "demo/scripted", "temperature": 0},
                             "tools": [], "engine_options": {"max_iterations": 1}}, identifier="coder")
        await workflows.acreate(coding_workflow(max_attempts), identifier="code-review")
        project = await backend.projects.aload(project.id)
        session = await project.sessions.acreate("Implement addition")
        request = await session.run.submit("Implement add(a, b), returning the arithmetic sum of two numbers.", engine="graph")
        run = await request.wait()
        data, steps = await run.aget_data(), await run.steps.alist()
        root = next((step for step in steps if step.kind == "graph"), None)
        summary = {"mode": "live" if model else "scripted", "status": str(data.status),
                   "project": str(project.paths.root), "run_id": run.id, "file": str(output),
                   "error": data.error, "output": root.output.data if root and root.output else None}
        (workdir / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if display:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--model", help="LiteLLM model; credentials come from the existing provider environment")
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    if args.workspace:
        result = asyncio.run(run_demo(args.workspace, model=args.model, max_attempts=args.max_attempts))
    else:
        with TemporaryDirectory(prefix="llm-code-workflow-") as root:
            result = asyncio.run(run_demo(Path(root), model=args.model, max_attempts=args.max_attempts))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
