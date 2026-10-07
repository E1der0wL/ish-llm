"""ish 플러그인의 진입점과 LargeLanguageModel 공개 API. 백엔드는 실행과 조회에 동일한 서비스를 사용하고 종료 시 저장 작업과 이벤트 알림을 정리한다.

ish plugin entry point for one streaming request: python -m llm.llm --help."""

# The platform reads this literal with ast.literal_eval before importing us.
# requirements lists other ish plugins; dependencies lists Python distributions.
PLUGIN_META = {
    "name": "llm",
    "version": "0.1.0",
    "description": "Durable AI sessions and streaming LoopEngine execution for ish.",
    "author": "",
    "requirements": [],
    "dependencies": [
        "litellm>=1.100,<2",
        "jsonschema>=4.23,<5",
        "pydantic>=2,<3",
        "packaging>=23",
        "langgraph>=1.2.12,<2",
        "chromadb",
        "kuzu",
        "rank-bm25|rank_bm25",
        "Pillow|PIL",
    ],
}

import argparse
import asyncio
import os
import sys
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Union

from llm._platform import require_linux
from llm.core.models import ProjectConfig, Run, RunStatus, Session
from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef, ProjectActivityEvent
from llm.core.views import SessionRuntimeView, RunView
from llm.core.plans import ResumePlan, RecoveryPlan, RetentionPlan, RecoveryResult
from llm.core.results import EngineOutput, EngineDelta, ExecutionResult, CompletionResult
from llm.core.interactions import InteractionRequest, InteractionOption, InteractionResponse, InteractionView
from llm.providers.calls import ProviderCalls, ProviderLimits
from llm.services.runtime.output import OutputBuffer
from llm.engines.base import Engine, EngineEvent, EngineEventType
from llm.core.steering import RunInstruction, InstructionStatus, SteeringMode, SteeringTarget, SteeringRoute
from llm.engines.registry import EngineRegistry
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine, GraphNodeContext
from llm.engines.graph.tool import ToolNode
from llm.engines.graph.agent import AgentNode
from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.components.tools.builtin import BuiltinTools, BuiltinToolComponent
from llm.components.tools.component import ToolComponent
from llm.components.agents import AgentComponent
from llm.components.skills import SkillComponent
from llm.components.goals import GoalComponent, GoalData
from llm.components.refinement import RefinementComponent, RefinementData
from llm.components.prompts import PromptComponent
from llm.components.mcp import MCPComponent
from llm.components.rag import RAGComponent, EmbeddingModel, RerankModel
from llm.components.rag import TripleExtractor
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.memory import MemoryComponent, MemoryData, MemoryConflictError
from llm.components.vision import VisionComponent, VisionData, TesseractBackend
from llm.components.base import ProjectComponent
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunEvent, RunManager, RunErrorCode, RunRequestError
from llm.services.runtime.policies import RunPolicy, ProjectPolicyResolver
from llm.services.runtime.tools import ToolRuntime, ToolCall, ToolExecutionError, ToolApprovalRequired
from llm.services.runtime.processes import ProcessToolRunner
from llm.policies import CompletionPolicy
from llm.services.api import Projects
from llm.services.infrastructure.storage import StorageIO, drain_on_cancel
from llm.services.composition import BackendServices
from llm.services.history.conversation import MemoryConversations, MemoryConversationStore, conversation_store
from llm.services.runtime.events import EventSubscriptions


# 공개 진입점: 하나의 workspace와 이벤트 루프에 서비스/런타임을 묶는다.
class LargeLanguageModel:
    """Project → Session → Run → Step을 탐색하고 Engine·Component 확장을 연결하는 공개 백엔드.

    운영 관찰(읽기 전용)::

        snapshot = backend.observability.snapshot()
        snapshot = await backend.observability.asnapshot()
        print(snapshot["tools"]["requests"], snapshot["tools"]["executions"])
        print(snapshot["providers"]["active"], snapshot["events"]["dropped"])

    누적 통계는 backend 수명 동안만 유지한다. 현재 gauge는 기존 runtime owner에서 읽으며
    snapshot은 모델/Tool/파일 쓰기를 수행하지 않는다. privacy-safe fact만
    BackendServices(observability_sink=callback)으로 받을 수 있다. callback은 빠른 thread-safe
    동기 함수여야 하며 예외는 실행 결과에 영향을 주지 않는다.

    하나의 프로세스와 이벤트 루프에서 사용한다. 생성자는 파일을 쓰거나 모델을
    호출하지 않으므로 .ishrc.py에서 만들 수 있다. 종료 시 shutdown()을 await하거나
    async with를 사용한다. 아래 도메인별 코드는 살아 있는 backend와 그 핸들에 대한
    사용 예시이며, 모든 블록을 차례대로 실행하는 스크립트는 아니다.

    Project 실행 타임라인 (읽기 전용 파생 인덱스)::

        events = await project.aactivity(limit=100)
        # 동기 조회: project.activity(limit=100, newest_first=False)
        # events: tuple[ProjectActivityEvent, ...]; time/event/source/status/code만 포함한다.
        # source.session_id/run_id/step_id로 기존 도메인의 상세 결과를 조회한다.
        # limit 미지정 시 전체. 인덱스 누락은 실행 실패가 아니며 복구/재실행의 근거가 아니다.

    사용자 승인/확인 요청 (Loop, Graph, 중첩 Agent 공통)::

        requests = await paused_run.ainteractions(pending_only=True)
        request = requests[0]
        # request.options, category, priority, risk, recommended_option_id로 UI를 구성한다.
        response = request.respond("approve")  # 사용자의 실제 선택 후 생성
        await paused_run.arespond(response)    # 원자적 응답 저장, 아직 실행하지 않음
        resumed = await session.run.resume(paused_run.id, engine=paused_run.engine)
        result = await resumed.wait()

    InteractionRequest/InteractionOption/InteractionResponse는 llm 플러그인의 공개 클래스다.
    EngineEvent.interaction으로 저장 완료된 요청을 실시간 관찰할 수 있으며, UI 재접속 시
    Run.ainteractions()/ainteraction_responses()로 복원한다. 권장 선택지는 자동 승인이 아니다.
    거절은 request.respond("deny"), Graph의 명시적 resume_schema 입력은
    request.respond("approve", value={...})로 전달한다. Tool 인자는 승인 응답으로 변경할 수 없다.

    장시간 실행/출력 저장 조율::

        services = BackendServices(
            provider_limits=ProviderLimits(max_active=4, max_waiting=16, wait_seconds=10),
            output_buffer=OutputBuffer(batch_size=32, max_delay=0.025, max_chars=65536),
            output_index_stride=128,
        )
        backend = LargeLanguageModel("workspace", services=services)
        print(backend.provider_calls.stats)  # 실제 종료되지 않은 호출도 active에 포함

    기본 batch_size=1은 즉시 저장이다. 묶음 모드도 Tool 승인/체크포인트 경계를 보존한다.
    file Project 백업은 모든 Session 런타임을 해제한 다음 호출한다::

        await session.run.shutdown()
        archive = await project.abackup("backups/project-copy")
        # 동일 ID가 없는 별도 backend에서만 복원한다.
        restored = await other_backend.projects.arestore_backup(archive)
        upgraded = await backend.projects.aupgrade_backup(
            archive, "backups/upgraded", transform=convert_project_copy)

    변환 함수는 사본의 Path를 받는 신뢰한 개발자 코드다. 원본을 변경하지 않고 현재
    저장 형식으로 검증한다. memory/외부 대화는 휴대 가능한 파일 백업에서 거부한다.
    자세한 보장/제한과 저장 버전 정책은 docs/llm/operational-storage.md를 참고한다.

    생성 및 최소 실행 예제::

        from llm.llm import LargeLanguageModel, LoopEngine, ProjectConfig, RunStatus
        from llm.services.query import Query

        async def example(model: str):
            async with LargeLanguageModel(
                "./workspace",
                engines={"chat": LoopEngine()},
            ) as backend:
                project = await backend.projects.acreate(
                    "도우미",
                    config=ProjectConfig(parameters={"engines": {"chat": {'config': {'completion': {'model': model}}, 'policy': {'max_iterations': 5}}}}),
                    conversation_storage="file",
                )
                session = await project.sessions.acreate("첫 대화")
                request = await session.run.submit("안녕!", engine="chat")
                run = await request.wait(timeout=60)
                result = await run.aresult()
                if result.status == RunStatus.COMPLETED:
                    print((await run.aresponse()).content)
                else:
                    print(result.status, result.error_code, result.error)

    생성자 설정:
        workspace는 Project들을 담는 작업 공간이다. engines는 이름→Engine 인스턴스
        매핑이며 생략하면 "loop"가 등록된다. 등록과 실행 선택은 별개라서 submit에는
        항상 engine을 지정해야 한다. 모델명과 인증은 사용하는 공급자에 맞게 설정한다.
        components는 제공할 Component 인스턴스 목록이며 명시하면 기본 목록을 대체한다.
        실제로 사용할 종류는 Project 생성의 components=["tools", ...]에서 선택한다.
        services=BackendServices(...)로 저장소·계산기·로그·이벤트 처리기를 주입한다.
        실행 정책은 ProjectConfig.policies, 구현체 인자는 parameters에 저장한다.
        conversation_storage는 새 Project의 저장 방식이며 기존 Project의 명시된 선택은 보존한다.
        on_event(run, event), on_run_event(event)는 실행 관찰 콜백이다.

    동기/비동기 사용 규칙:
        create/load/list/save 등의 동기 API에는 acreate/aload/alist/asave처럼
        a 접두사의 비동기 API가 있다. UI와 async 코드에서는 비동기 API를 권장한다.
        예를 들어 project = backend.projects.create("작업")도 가능하다.
        run.submit/start/wait_idle/interrupt/shutdown과 request.wait는 원래 비동기다.
        핸들의 data는 최신 도메인 데이터의 분리된 사본이며 수정만으로 저장되지 않는다.
        async 코드에서는 await handle.aget_data()를 사용하고 변경은 save/asave로 한다.

    Project — 생성·조회·수정·복제·삭제::

        # 각 컴포넌트의 JSON 설정을 ProjectConfig로 전달할 수 있다.
        project = await backend.projects.acreate(
            "문서 작업", components=["rag", "memory"],
            config=ProjectConfig(policies={"output": {"batch_size": 16}}, parameters={"components": {
                "rag": {'config': {'chunk_size': 1200, 'search': {'limit': 8}}},
                "memory": {'config': {'search_limit': 5}},
            }}),
        )
        # 명시된 client 설정 위에 Project 설정을 적용한다.
        # configure() 편의 API도 항상 같은 Project 설정을 갱신한다.
        view = await project.adescribe_config()
        print(view["components"]["rag"]["effective"]["sources"])
        config = ProjectConfig(view["project"]["config"])
        config.parameters["components"]["rag"]["config"]["search"]["limit"] = 10
        await project.asave(config=config, expected_version=view["config_version"])

        # 기본 Project/Session과 마지막 선택은 호출 애플리케이션이 소유한다.
        # acreate는 매번 새 Project를 만들며 재사용은 아래 aload(id)로 명시한다.

        project = await backend.projects.acreate(
            "연구", config=ProjectConfig(parameters={"engines": {"loop": {'config': {'completion': {'model': model}}}}}),
            components=["tools", "skills", "agents", "workflows"],
            conversation_storage="memory",
        )
        project = await backend.projects.aload(project.id)
        projects = await backend.projects.alist(include_deleted=True, query=Query(limit=20))
        await project.asave(title="연구 노트")
        config = (await project.aget_data()).config
        config["parameters"]["engines"]["loop"]["config"]["completion"]["temperature"] = 0.2
        await project.asave(config=config)
        project_copy = await project.aclone(title="연구 사본")
        await project.adelete()                 # 소프트 삭제
        await project.arestore()                # 복원
        # await project.adelete(permanent=True)  # 프로젝트 파일까지 영구 삭제

        # Session이 없는 프로젝트에서만 저장 방식을 바꿀 수 있다.
        await project.asave(conversation_storage="file")

    Project의 id/paths로 식별자와 경로를 조회한다. config는 열린 JSON 설정이며
    save(config=...)는 전체 설정을 교체한다. 복제·삭제 전에는 소속 Session의 런타임을
    각각 await session.run.shutdown()으로 해제한다. wait_idle()만으로는 해제되지 않는다.
    저장 선택은 project.json에 남는다. memory의 대화와 대기 요청은 백엔드 종료 시
    사라지지만 Project/Session/Run/Step 파일은 유지된다. 기존 대화를 자동 이전하지 않는다.

    Session / Conversation — 독립 대화 세션 관리::

        session = await project.sessions.acreate("코드 검토", config={"parameters": {"engines": {"loop": {"config": {"completion": {"temperature": 0}}}}}})
        session = await project.sessions.aload(session.id)
        sessions = await project.sessions.alist(include_deleted=True, query=Query(limit=20))
        await session.asave(title="검토 세션", metadata={"language": "ko"})
        messages = await session.aconversation(query=Query(limit=50))
        await session.run.shutdown()
        await session.asave(config={"parameters": {"engines": {"loop": {"config": {"completion": {"temperature": 0.1}}}}}})
        session_copy = await session.aclone(title="검토 사본")
        await session.adelete()
        await session.arestore()
        # await session.adelete(permanent=True)

    Session의 id/paths/data를 조회할 수 있다. 제목·메타데이터는 실행 중에도 수정할 수
    있지만 config 변경·복제·삭제·복원 전에는 런타임을 해제한다. config와 metadata는
    전달한 사전으로 교체한다. Session 복제는 대화/설정을 복사하며 Run/Step 이력은 복사하지
    않는다. 대화는 Session에 속하고 새 입력은 conversation에 직접 쓰지 않고 submit한다.

    Request / Run — 요청 제출·한 건의 완료 대기·실행 제어::

        request = await session.run.submit("이 코드를 검토해줘.", engine="chat")
        request = await session.run.arequest(request.id)  # 사용자 Message ID로 요청 조회
        message = await request.aget_data()
        pending_run = await request.aget_run()        # 아직 실행 전이면 None
        pending_result = await request.aresult()      # Run이 없으면 None
        run = await request.wait(timeout=60)          # 이 요청의 최종 RunHandle
        run = await session.run.aload(run.id)
        runs = await session.run.alist(query=Query(status="completed", limit=20))
        snapshot = await run.aget_data()
        response = await run.aresponse()              # Assistant Message
        result = await run.aresult()                  # ExecutionResult
        print(run.id, snapshot.engine, response.content)

        await session.run.start()       # 저장된 대기 요청 복구; submit도 필요 시 시작한다.
        await session.run.wait_idle()   # 해당 Session의 전체 요청 큐가 비워질 때까지 대기
        interrupted = await session.run.interrupt()  # 현재 Run만 중단; 대기 요청은 보존
        await session.run.shutdown()    # 해당 Session 런타임 해제; 이후 submit으로 다시 시작

    submit의 반환값은 Run이 아닌 RequestHandle이다. 같은 Session은 순차 실행하고 서로
    다른 Session은 동시에 실행할 수 있다. request.wait()는 실패/중단한 Run도 반환하므로
    result.status를 확인한다. 대기 timeout이나 대기 호출 취소가 Run을 중단하지는 않는다.
    동기 조회는 request.data/run/result, run.data/engine/response/result를 사용한다.
    memory 대화가 소멸하면 이전 Run 메타데이터는 남아도 응답 Message 조회는 KeyError다.

    실행 중 추가 지시 — UI가 Run 시작 알림에서 받은 ID로 호출한다::

        instruction = await session.run.steer(active_run_id, "결과를 표로 정리해줘.")
        active_run = await session.run.aload(active_run_id)
        instructions = await active_run.ainstructions()

    단독 Loop에서 같은 RUNNING Run의 다음 completion 입력에 반영한다. 진행 중인 Tool
    묶음은 먼저 끝내며 일반 submit은 여전히 다음 Run이다. Graph는 실행 대상을 지정한다::

        targets = await active_run.ainstruction_targets()
        # UI에서 사용자가 선택한 accepting=True 소비자 ID 목록을 전달한다.
        instruction = await session.run.steer(active_run_id, "근거도 포함해줘.",
                                               targets=selected_target_ids)

    Graph/중첩 Agent는 지정한 자식 실행에만 전달하며 Pipeline 라우팅은 미지원이다.
    미시작 Agent는 실행 스냅샷의 SteeringRoute로 한 번 예약한다::

        routes = await active_run.ainstruction_routes()
        # UI가 routes 중 선택한 SteeringRoute 객체 목록이다.
        instruction = await session.run.reserve_instruction(
            active_run_id, "이번 검토에 예외 처리도 포함해줘.", targets=selected_routes)

    경로별 다음 실행 한 번만 소비하며 미사용 예약은 unapplied와 사유를 남긴다.
    재개 시 미사용 예약은 이전하지 않고 적용한 예약은 기존 문맥만 복원한다.
    지속 변경은 interrupt 후 종료를 기다려 Workflow를 수정하고 submit으로 새로 시작한다.
    접수 마감/미지원 대상은 steering_unavailable로 거절한다. 대상별 상태는 instruction.targets,
    전체 상태는 pending/applied/unapplied/partially_applied다. applied는 입력 준비 확정이며
    provider 수신이나 모델의 이행을 보장하지 않는다. applications와 reason도 조회한다.
    저장 후 STEERING_CHANGED(metadata.instructions, targets 또는 routes)로 알린다. 알림 누락이나
    API 대기 취소 뒤에는 해당 조회 API로 재조회한다. 기존 Run/Tool 정책과 interrupt는 유지된다.

    운영 한도 / UI 대기열::

        services = BackendServices(
            tool_runtime=ToolRuntime(),
            conversation_cache_size=32,
        )
        backend = LargeLanguageModel("./workspace", services=services, engines={"loop": LoopEngine()})
        project = await backend.projects.acreate(config=ProjectConfig(policies={
            "tools": {"max_calls": 80},
            "context": {"mode": "full"},
            "run": {"max_queued": 20, "timeout_seconds": 1800},
        }, parameters={"engines": {"loop": {'policy': {'completion': {'max_tokens': 32000, 'reserve_tokens': 4000, 'counter': 'model_default'}, 'provider': {'max_attempts': 2}}}}}))
        await project.aconfigure_policies({"context": {"mode": "recent", "max_turns": 10}})
        config = await project.adescribe_config()  # project/components와 UI용 policy_schema
        status = await session.run.astatus(queued_limit=20)
        cancelled = await request.cancel()  # 실행 전 요청만 취소; 실행 중이면 False

    RunPolicy의 기본값은 무제한이다. ToolRuntime에는 async authorize(call),
    runner(tool, call), classify를 주입할 수 있다. allowed_tools는 Project 정책이다. Loop와 Graph Agent/Tool이
    Run 단위 예산을 공유한다. parameters.engines[등록 이름].policy.completion는 매 Loop 호출 전에
    CompletionPolicy로 과거 턴을 선택한다. 토큰 계산 함수는 BackendServices.token_counters에
    이름으로 등록한다. 공통 사용량 제한은 policies.usage.counter를 별도로 선택한다.
    공통 정책 사본은 Run.metadata.policies에 남으며 변경은 다음 Run부터 반영한다.
    현재 Tool 문맥까지
    넘치면 context_budget_exceeded로 실패하고 원문은 유지한다. 자세한 사용 예와 운영
    한계는 docs/llm/runtime-reliability.md를 따른다.

    장시간 실행 / 복구 / UI 편집::

        checkpoint = await run.acheckpoint("loop")
        resumed = await session.run.resume(run.id, engine="loop")
        # 불확실한 Tool은 retry_nodes, 승인 대기는 decisions를 명시한다.
        view = await run.aview(after=0, limit=200)
        snapshot = await project.components.workflows.asnapshot("flow")
        await project.components.workflows.asave("flow", snapshot["data"],
            expected_version=snapshot["version"])
        plan = await project.aretention()  # 기본 삭제 없음; 적용은 별도 명시
        job = await project.components.rag.aenqueue_document(title="설명서", content=markdown)
        await project.components.rag.arun_job(job["id"])

    policies의 tool_retry/usage/retention과 각 Engine·Component의 provider 설정으로 조절한다.
    정상 None Tool 결과는 재사용하고 불확실한 효과는 자동 재실행하지 않는다.
    Graph 안의 Loop는 노드 경계로 재개한다. docs/llm/long-running.md에 사용 조건을 설명한다.

    Tool 격리와 동일 외부 작업 결과 재사용::

        runner = ProcessToolRunner(
            {"external_job": ["/usr/bin/python3", "-I", "/opt/tools/worker.py"]},
            cwd="/srv/tool-work", read_only_paths=["/opt/tools"], isolation="sandbox")
        services = BackendServices(tool_runtime=ToolRuntime(
            runner=runner, operation_key=lambda call: call.arguments["operation_id"]))
        record = await session.run.aoperation("business-id")
        await session.run.shutdown()
        await session.run.areconcile_operation("business-id", result={"receipt": "confirmed"},
                                           evidence="외부 시스템에서 완료 확인")

    worker는 stdin ToolCall JSON → stdout JSON 계약을 사용한다. sandbox는 Linux Bubblewrap
    필수이며 사용 불가 시 거부한다. 명시적 process 모드는 Linux 프로세스 그룹을 종료하며
    파일/네트워크 보안 sandbox가 아니다. 키는 Session 범위이며 완료 결과를 재사용하고
    불확실한 started는 차단한다. 제공자 idempotency 규약에 call.idempotency_key를 연결한다.
    기본 핸들러/내장 shell은 자동으로 격리되지 않는다. docs/llm/process-isolation.md를 참고한다.

    공통 조회·진단·계획 데이터::

        status = await session.run.astatus()          # SessionRuntimeView
        print(status.queued_count, status.unfinished_work)
        view = await run.aview()                   # RunView: run/outputs/cursor/events
        payload = view.to_dict()                  # UI 전송용 JSON 사전
        restored = RunView.from_dict(payload)
        plan = await session.run.resume_plan(run.id, engine=run.engine)  # ResumePlan
        for issue in plan.blockers:               # Diagnostic
            print(issue.code, issue.message, issue.source)
        recovery = await project.arecovery()      # RecoveryPlan
        retention = await project.aretention()    # RetentionPlan
        # 검토 뒤 해당 작업 API에 expected_version=recovery.version 등을 전달한다.
        for step in await run.steps.alist():
            print(step.progress, step.diagnostic) # OperationProgress / Diagnostic 또는 None
        progress = await project.components.rag.ajob_progress(job_id)

    Diagnostic/ResourceRef/OperationProgress, SessionRuntimeView/RunView,
    ResumePlan/RecoveryPlan/RecoveryResult/RetentionPlan은 공개 데이터 클래스다.
    JSON은 to_dict/from_dict로 변환한다. dict 접근 별칭은 제공하지 않는다.
    진단의 severity나 진행률로 재시도·승인·완료를 결정하지 않는다. 도메인 상태와
    실행 정책이 원본이다. 프로젝트 설정, 컴포넌트 정의, 공급자 인자는 열린 dict를 유지한다.

    승인 UI와 명시적 재개::

        views = await run.ainteraction_views()  # InteractionView: 응답/실행 상태 분리
        request = views[0].request
        await run.arespond(request.respond("approve"))
        plan = await session.run.resume_plan(run.id, engine=run.engine)
        if plan.can_resume:
            resumed = await session.run.resume(run.id, engine=run.engine)
        # 만료/취소 요청은 acancel_interaction/arenew_interaction으로 관리한다.

    응답 저장은 실행을 시작하지 않는다. Project policies.approval의 기본값은 비활성이다.
    자동 승인은 Project의 동일 risk_scheme 임계값으로 판단하고 정책 ID를 남긴다.
    INTERACTION_CHANGED 알림 유실 시 ainteraction_views로 다시 조회한다.
    Graph cleanup_timeout 초과 작업은 astatus().unfinished_work로 조회하고 실제 종료까지
    Session 소유권을 유지한다. Memory의 aconsolidate는 검토된 후보를 버전 검사 후 통합하며
    중단된 병합은 apending_consolidations/arecover_consolidations로 복구한다.
    설정과 제약은 docs/llm/domain-hardening.md를 참고한다.

    Engine — 전략 등록과 명시적 선택::

        backend.engines.register("review", LoopEngine())
        # 반복 한도는 Project parameters.engines.review.policy.max_iterations에 저장한다.
        names = backend.engines.names()
        engine = backend.engines.get("review")
        request = await session.run.submit("검토해줘.", engine="review")

    이름은 중복 등록할 수 없다. GraphEngine 등 개발자가 만든 Engine도 같은 방식으로
    등록한다. Engine 실행은 session.run.submit을 거쳐야 Run/Step/대화 기록과 연결된다.
    run.engine은 저장된 전략 이름이며 Engine 객체가 아니다. Engine 자체는 영속 파일을
    쓰지 않고 이벤트를 전달한다. Component 정의를 저장하는 것만으로 실행되지는 않는다.

    Step — Run 내부 실행 단위 조회::

        steps = await run.steps.alist(query=Query(limit=50))
        if steps:
            step = await run.steps.aload(steps[0].id)
            print(step.kind, step.status, step.metadata)

    반환값은 Step 도메인 객체다. Facade의 Step API는 조회 전용이며 생성·상태 전이는
    Engine 이벤트를 받는 StepEventRecorder/StepManager가 담당한다.

    Results — Run의 사용량·종료 이유를 상위 도메인에서 조회::

        result = await run.aresult()
        print(result.total_tokens, result.finish_reasons, result.duration_seconds)
        session_results = await session.results.alist(include_running=True, query=Query(limit=20))
        project_results = await project.results.alist(include_deleted=True, query=Query(limit=20))
        same_result = await project.results.aload(run.id)
        same_result = await session.results.aload(run.id)

    결과는 Run에서 계산한 조회 뷰이며 별도 Project 결과 파일을 만들지 않는다.
    completions에는 각 모델 호출의 관찰값이 있다. 사용량을 모르면 total_tokens는
    0이 아닌 None이다. Query(after=마지막_ID, status=상태, offset=0, limit=20,
    descending=True)로 목록을 제한한다. after는 숫자 인덱스가 아닌 객체 ID다.

    Component — 선택한 프로젝트 기능의 설정과 데이터 CRUD::

        await project.components.aselect(["tools", "skills", "agents", "workflows"])
        skills = await project.components.aget("skills")  # 동기: project.components.skills
        identifier = await skills.acreate({"instructions": "변경된 코드를 검토한다."})
        data = await skills.aload(identifier)
        records = await skills.alist()                # {식별자: 데이터} 사전
        data["description"] = "리뷰 지침"
        await skills.asave(identifier, data)          # 전체 데이터 교체
        updated = await skills.aupdate(identifier, {"description": "새 리뷰 지침"})
        await skills.aconfigure({"config": {"ui": {"label": "검토"}}})
        configuration = await skills.aget_config()
        await skills.adelete(identifier)              # 정의 파일 삭제
        await project.components.aremove("skills")   # 선택 해제; 데이터는 유지
        # await project.components.aremove("skills", permanent=True)

    select는 전체 선택 목록을 교체하고 필요한 디렉토리를 만든다. 등록되어 있고 선택된
    Component만 접근할 수 있다. project.components["skills"]도 지원한다. create/acreate의
    identifier=로 식별자를 지정할 수 있다.
    configure는 전체 설정을 교체하고 update는 최상위 키를 병합한다. 데이터는 Component
    검증 규칙을 따르는 열린 JSON 사전이다. 기본 제공 종류는 tools/skills/mcp/rag/
    agents/workflows/memory/prompts/vision/goals/refinement이며 정의 저장과 실제 실행은 별개다.

    Goal과 Refinement — 실행 소유권 없이 목적과 개선 제안을 관리::

        goals = await project.components.aget("goals")
        goal_id = await goals.acreate({"title": "검증", "objective": "검증된 변경만 적용",
            "scope": {"type": "project"}, "status": "active"})
        view = await goals.asnapshot(goal_id)
        await goals.alink_run(goal_id, session.id, run.id, relation="contributes_to",
                              expected_version=view["version"])
        refinement = await project.components.aget("refinement")
        proposals = await refinement.alist()

    Refinement는 create → validate → approve → apply로 대상을 변경하며 rollback에도
    CAS를 적용한다. 자동 모델 호출은 없다. 모델의 제안은 일반 Run의 refinement_propose
    Tool로 저장하며, apply_tools 정책을 켠 적용 Tool은 기존 ToolExecutor 승인 경계를 따른다.
    상세 예제는 각 Component README에 있다.

    Vision — 등록된 이미지 전처리·OCR·모델 해석::

        # Project 생성 시 components에 "vision"을 선택한다.
        vision = await project.components.aget("vision")
        await vision.aconfigure({"config": {"ocr": {"backend": "tesseract"}}})
        image = await vision.aimport_image("/path/screen.png", title="화면")
        result = await vision.aocr(image["id"])
        document = await vision.aextract_document(image["id"], mode="ocr", title="화면 텍스트")
        # rag도 선택·설정되어 있다면 await project.components.rag.aadd_document(**document)

    backend 미설정이면 OCR 호출에서 backend를 명시해야 한다. VLM은 vision.config.completion의
    모델 설정 후 aanalyze(image_id, prompt=...)로 호출한다. 이미지 삭제는 출처 보존을 위한
    tombstone이며 개별 bytes를 회수하지 않는다. 자세한 계약은 components/vision/README.md.

    Memory — 프로젝트 장기 기억 (휘발성 대화 저장 옵션과는 별개)::

        # 선택 목록에 "memory"를 포함해 Project를 생성한다.
        memory = await project.components.aget("memory")
        identifier = await memory.acreate({"content": "설명은 한국어로 작성한다.", "kind": "preference"})
        record = await memory.aload(identifier)
        record = await memory.aupdate(identifier, {"tags": ["응답"]}, expected_revision=record["revision"])
        hits = await memory.asearch("한국어")
        history = await memory.ahistory(identifier)
        deleted = await memory.adelete(identifier, expected_revision=record["revision"])
        await memory.arestore(identifier, expected_revision=deleted["revision"])

    API 생성 레코드는 confirmed 상태이며 모델 Tool의 작성 상태는 policy.tool_write_status로 반드시 지정한다.
    선택 시 memory_search/get/create/update/delete Tool을 자동 제공한다. 모델 수정도 revision
    검사를 거치고 출처는 실행 문맥에서 기록한다. 자세한 정책은 docs/llm/memory.md를 참고한다.

    Memory — 선택적 장기 문맥 처리::

        await memory.aconfigure({
            "config": {"processing": {
                "completion": {"model": "provider/model"}, "priority": 100,
                "summary_chars": 2000, "recall_limit": 5, "extract_scope": "session",
            }},
            "policy": {"processing": {
                "summarize": True, "extract": True,
                "keep_turns": 8, "context_chars": 6000, "summary_after_chars": 12000,
                "model_input_chars": 16000, "max_summary_calls": 2, "max_candidates": 5,
            }},
        })
        summary = await memory.asummary(session.id)
        review = await memory.areview(session_id=session.id)
        identifier = await memory.acreate({"content": "다음은 검증 단계", "kind": "work_state",
                                           "scope": "session", "session_id": session.id})
        record = await memory.aload(identifier, session_id=session.id)

    자동 검색/Tool 미리보기는 policy.processing에서 명시적으로 활성화한다. 위 숫자는 예제의 선택이다. 보조 모델 요약/추출은 설정으로 켜며 원본 대화와
    Tool 결과를 덮어쓰지 않는다. 주요 도메인에는 Memory 전용 실행 로직이 없다.
    예산, 중첩 Engine, 오류 정책은 docs/llm/memory-processing.md를 참고한다.

    RAG / GraphRAG — 문서와 검색 인덱스를 함께 관리::

        from llm.components.rag import RAGComponent, EmbeddingModel, TripleExtractor

        # 기본 모델 설정은 ProjectConfig로 전달하고 필요한 실행 함수만 호스트에서 주입한다.
        project = await backend.projects.acreate("문서", components=["rag"],
            config=ProjectConfig(parameters={"components": {"rag": {
                "config": {
                    "embedding_params": {"model": embedding_model, "api_key": api_key},
                    "extraction_params": {"model": model, "api_key": api_key},
                    "chunk_size": 1000, "extraction_batch_size": 8,
                    "search": {"method": "hybrid", "expand": "section", "limit": 5,
                               "candidate_count": 20, "rrf_constant": 60,
                               "max_hops": 2, "relation_limit": 30},
                },
                "policy": {"embedding_concurrency": 2,
                           "extraction": {"failure_policy": "required"}},
            }}}))
        rag = await project.components.aget("rag")
        document = await rag.aadd_document(title="운영 안내", content=markdown_text)
        result = await rag.asearch("백업 정책", method="hybrid", expand="section")
        hits, relations = result["documents"], result["relations"]
        graph = await rag.agraph_search("아틀라스", max_hops=2)
        updated = await rag.aupdate_document(document["id"], content=updated_markdown,
                                             expected_revision=document["revision"])
        documents = await rag.alist_documents()
        await rag.adelete_document(document["id"], expected_revision=updated["revision"])

    rag 하나가 임베딩과 트리플을 함께 저장한다. 등록/수정에는 embedding과 extractor가 필요하다.
    asearch는 documents/entities/relations/sources를 함께 반환한다. 문서 목록만 필요하면
    asearch_documents를 사용한다. 관계 탐색에 별도의 질의 전처리 모델 호출은 필요 없다.
    aadd/aupdate_document는 색인 완료 후 dict를 반환한다. 작업 ID를 반환하는 API가 아니다.
    새 세대가 완성되기 전에는 이전 문서/색인을 유지한다. 동시 변경 충돌은 RAGConflictError로
    보고하며 자동 재실행하지 않는다. 문서 API와 기존 정의 acreate/asave/adelete는 별개다.
    DB 작업은 공유 StorageIO에서 수행하고 모델 준비는 잠금 밖에서 await한다. Component는
    Run/Step을 생성하지 않는다. Engine/Tool에서 사용할 때 해당 실행의 이벤트로 기록한다.

    Component별 편의 API (add 정의와 workflow_id의 그래프가 저장되어 있는 경우)::

        tools = await project.components.aget("tools")
        await tools.aenable("add")
        selected = await tools.aenabled()
        await tools.adisable("add")
        await tools.aset_enabled(["add"])

        agents = await project.components.aget("agents")
        agent_id = await agents.acreate({"engine": "loop", "purpose": "검토", "completion": {"model": model}})
        prompt = await agents.aprompt(agent_id)
        await agents.aupdate_prompt(agent_id, "정확하게 검토한다.", expected_revision=prompt["revision"])
        snapshot = await agents.asnapshot(agent_id)
        definition = snapshot["definition"]
        definition["policy"] = {"timeout_seconds": 120}
        await agents.arevise(agent_id, definition, expected_revision=snapshot["revision"])

        # Graph 처리기는 별도로 Agent 업무 엔진을 명시적으로 등록한다.
        agent_node = AgentNode(engines={"loop": LoopEngine()})
        # GraphEngine(handlers={"agent": agent_node})를 백엔드 engines에 등록한다.

        workflows = await project.components.aget("workflows")
        await workflows.avalidate(workflow_id)         # 저장된 버전 1 그래프 검증
        graph = await workflows.agraph(workflow_id)   # 수정 가능한 WorkflowGraph 사본
        await workflows.asave(workflow_id, graph.to_dict())

    Graph 체크포인트 / 명시적 재개::

        request = await session.run.submit("작업", engine="graph",
                                           engine_options={"workflow": workflow_id})
        run = await request.wait()  # pause_before 노드에서는 status == RunStatus.PAUSED
        checkpoint = await run.acheckpoint()  # 헤더 + 노드별 started/completed/waiting
        resumed = await session.run.resume(run.id, engine="graph")
        next_run = await resumed.wait()

    workflow는 저장된 Workflow ID이며 생성자 대신 매 submit에서 명시한다.
    engine_options는 QUEUED 요청과 Run에 저장된다. 정의는 Run 시작 시 읽고 실행 중 유지한다.
    재개는 원본 engine_options를 복원하며 Workflow를 바꾸지 않는다.
    재개는 원본을 연결하는 새 Run이다. 완료된 노드는 호출하지 않는다. started 처리 노드는
    부작용이 불확실하므로 UI가 확인한 키만 retry_nodes=[...]로 승인해야 한다.
    Workflow/Agent/Tool 정의와 실행 설정이 바뀌면 거부한다. memory 대화가 종료로 소실된
    뒤에는 재개하지 않는다. 상세 계약과 UI 저장 예제는 docs/llm/graph-checkpoints.md를 따른다.

    RAG/GraphRAG 검색 Tool — Project에서 컴포넌트를 선택하면 자동 제공::

        project = await backend.projects.acreate("검색", components=["rag"])
        # 문서·관계·출처를 함께 반환하는 rag_search를 LoopEngine에 제공한다.

    검색 문서/모델은 각 컴포넌트에 먼저 구성한다. LoopEngine이 모델의 검색 Tool 호출과
    결과 전달을 수행하며 기존 Run에 Tool Step이 기록된다. 별도 활성화 API나 비활성화 옵션은 없다.

    Project Python Tool — 소스 관리와 명시적 준비::

        await project.components.tools.acreate({"source": source_text}, identifier="search")
        definition = await project.components.tools.aprepare("search")
        await project.components.tools.aenable("search")

    tools/search/search.py의 @tool async main에서 설명·입력 schema·실행 계약을 파생한다.
    requirements.txt가 있으면 prepare만 ish 공용 plugin/lib에 없는 dependency를 설치한다.
    CRUD/clone/backup은 import하지 않으며 Run 중 자동 설치는 없다.
    소스 관리·prepare·활성화는 신뢰한 Host/UI가 담당한다.

    Events / 수명 관리 — 스트리밍 관찰과 종료::

        from llm.llm import EngineEventType

        def on_text(run, event):
            if event.type == EngineEventType.TEXT_DELTA and event.delta.visibility == "user":
                print(event.delta.text, end="", flush=True)

        def on_run(event):
            print(event.run.id, event.type)

        unsubscribe = backend.events.subscribe(on_text, channel="engine")
        backend.events.subscribe(on_run, channel="run", delivery="queued", buffer_size=64)
        ui_subscription = backend.events.subscribe(
            on_text, delivery="queued", buffer_size=64,
            overflow="drop_oldest", callback_timeout=2)
        # ui_subscription.stats["dropped"]가 늘면 run.aoutput_events(after=cursor)로 복구한다.
        # 요청을 실행한 뒤:
        await backend.events.flush()
        unsubscribe()
        await unsubscribe.aclose()  # 실행 중인 콜백 완료와 참조 회수까지 대기
        await ui_subscription.aclose()
        await backend.shutdown()

    구독 해제는 대기 알림을 폐기하고 새 전달을 중단한다. 실행 중인 콜백은 취소하지 않는다.
    aclose는 해제와 정리를 함께 기다리며, 자기 콜백 안에서는 unsubscribe()만 사용한다.
    timeout을 명시하지 않은 콜백이 끝나지 않으면 정리도 기다린다. 해제 후 stats는 유지된다.

    공통 출력 API::

        result = await run.aresult()  # ExecutionResult: Run 상태/사용량/최종 출력
        if result.output is not None:
            print(result.output.text, result.output.data)
        for step in await run.steps.alist():
            if step.output is not None:
                print(step.kind, step.output.data)
        partial_outputs = await run.aoutputs()  # EngineOutput, 중단된 부분 출력 포함
        changes = await run.aoutput_events(after=0, limit=100)

    EngineEvent.delta는 EngineDelta, EngineEvent.output은 EngineOutput이다.
    UI는 (run.id, output_id)를 키로 삼고 visibility='user'/'internal'을 구분한다.
    delta.operation은 append/replace이며 sequence는 Run 전체 출력의 저장 순번이다.
    OUTPUT 및 STEP_COMPLETED의 output으로 카드를 확정한다. final=True는 해당
    출력의 확정이며 Run 완료와는 별개다. Run 상태는 result.status/RunEvent로 확인한다.
    누락 복구 시 마지막으로 연속 적용한 순번 이후부터 재조회하고 중복 순번을 제거한다.
    최상위 출력은 Run, Agent/중첩 Graph/Tool 결과는 소유 Step에 남는다.
    저장 형식과 UI 적용 예제는 docs/llm/engine-output.md를 따른다.

    Project 설정 폼과 운영 API::

        schema = backend.describe_project_config(components=["tools", "rag", "memory"])
        # properties.config / properties.config.properties.parameters.properties.components에서 타입·허용값·제약 조회
        config = await project.adescribe_config()
        values, schema = config["values"], config["schema"]
        # values.config.parameters["components"]는 명시된 값만 포함한다. 없는 키는 미설정이다.
        # config.project.config는 저장 원본이다. 전체 설정 후보를 저장 전에 검증한다.
        preview = await project.avalidate_config(config["project"]["config"],
                                                       expected_version=config["config_version"])
        await project.asave(config=preview["project"]["config"], expected_version=preview["config_version"])
        effective = config["effective_engines"]  # 적용값·출처·호스트 고정/가려진 값
        # effective["loop"]["values"], ["sources"], ["editable"], ["overridden"]
        session_view = await session.adescribe_config()  # Session 설정까지 적용한 Engine 값
        # RAG/Memory: config["components"][name]["effective"]
        # config_version/component_versions로 UI 편집 충돌을 검사한다.
        usage = await project.amodel_usage()  # Run + 독립 Component 모델 호출
        plan = await project.arecovery()     # 무결성/복구 미리보기; 자동 재실행 없음
        retention = await project.aretention()  # Session 또는 Run 단위 후보/보호 이유
        maintenance = await project.amaintenance()  # Component 소유 자료의 정리 후보
        # 복구/보관: expected_version=plan.version / retention.version
        # Component maintenance는 열린 사전 계약: expected_version=maintenance["version"]
        # 중단된 보관 삭제는 await project.arecover_retention()으로 마무리한다.

    선언된 설정과 등록 Component별 설정이 스키마에 포함된다. 열린 JSON 영역과
    공급자 고유 인자는 additionalProperties=True로 표시하며 모든 가능한 키를 추측하지 않는다.
    호스트 실행 객체의 설정은 별도다. 사용법은 docs/llm/operations-and-ui-config.md에 있다.
    Engine 설정 우선순위는 Project → Session → Agent → 명시적 호스트 값이다. missing은 상속하고 null은 상위 값을 덮어쓴다. 정책은 Project에만 저장한다.
    Graph·Loop·Pipeline과 RAG의 적용 규칙 및 변경점은 docs/llm/config-consistency.md를 따른다.

    관찰 콜백 안에서 같은 백엔드의 wait/shutdown을 기다리지 않는다. 사용자 정의 실행
    이벤트는 backend.event_handlers.register("custom_event", handler)로 연결한다.
    handler(context, event)는 async도 가능하며 실패하면 Run도 실패한다. 관찰 구독과 달리
    실행에 영향을 주는 계약이다. shutdown은 수락한 I/O와 알림까지 정리하며 이후에는
    새 백엔드를 생성해야 한다. 저수준 서비스는 project_manager/run_repository/
    step_manager로 접근할 수 있지만 UI에서는 위 Facade API를 사용한다.
    """

    def __init__(self, workspace: Union[str, Path] = "workspace", *,
                 components: Optional[Iterable[ProjectComponent]] = None,
                 engines: Optional[Mapping[str, Engine]] = None,
                 on_event: Optional[Callable[[Run, EngineEvent], None]] = None,
                 on_run_event: Optional[Callable[[RunEvent], None]] = None,
                 services: Optional[BackendServices] = None,
                 conversation_storage: Optional[str] = None) -> None:
        require_linux()
        self.workspace = Path(workspace).absolute()
        from llm.providers.runtime import configure_logging
        configure_logging(self.workspace)
        self.services = services if services is not None else BackendServices()
        self.policy_resolver = (self.services.policy_resolver if self.services.policy_resolver is not None
                                else ProjectPolicyResolver(self.services.token_counters))
        self.provider_calls = (self.services.provider_calls if self.services.provider_calls is not None
                               else ProviderCalls(self.services.provider_limits))
        if conversation_storage is not None:
            if conversation_storage not in ("file", "memory"):
                raise ValueError("conversation_storage must be 'file' or 'memory'")
            if self.services.conversations not in (conversation_store, "file"):
                raise ValueError("Choose conversation_storage or BackendServices.conversations, not both")
            self.services = replace(self.services, conversations=conversation_storage)
        self.engines = EngineRegistry()
        for name, engine in ({"loop": LoopEngine()} if engines is None else engines).items():
            self.engines.register(name, engine)
        # 제공 가능한 종류와 Project에서 선택한 종류는 다르다. 선택 시에만 디렉토리를 만든다.
        available = [ToolComponent(), SkillComponent(), MCPComponent(), RAGComponent(),
                     AgentComponent(engines=self.engines), WorkflowComponent(), MemoryComponent(), PromptComponent(),
                     VisionComponent(), GoalComponent(), RefinementComponent()] if components is None else components
        self.project_manager, self.run_repository, self.step_manager = self.services.build(
            self.workspace, available)
        self.project_manager.usage_counters = dict(getattr(self.policy_resolver, "token_counters", {}))
        self.events = EventSubscriptions()
        from llm.services.infrastructure.observability import Observability, ObservabilityView
        self._observability = Observability(sink=self.services.observability_sink)
        self.events.observability = self._observability
        self.observability = ObservabilityView(self._observability, self._runtime_gauges,
                                              self.provider_calls, self.events)
        self.event_handlers = self.services.event_handlers
        self.projects = Projects(self)
        self.project_manager.bind_config_validator(self.engines.validate_config)
        self.on_event = on_event
        self.on_run_event = on_run_event
        self._managers: dict[tuple[str, str], RunManager] = {}
        self._stopping: set[tuple[str, str]] = set()
        self._loop = None
        self._pid = os.getpid()
        self._closed = False
        self._closing = None
        self._storage = StorageIO(self.project_manager.ownership)
        self._storage_tasks: set[asyncio.Task] = set()
        self._storage_processes = set()
        self._observation_tasks: set[asyncio.Task] = set()
        self._storage_context = ContextVar("llm_storage_operation", default=False)

    def _runtime_gauges(self):
        """기존 Session runtime을 읽기만 한다. 도메인 파일 조회/복구는 하지 않는다."""
        active = queued = unfinished = 0
        for manager in tuple(self._managers.values()):
            runtime = manager._active
            unfinished += manager.pending_work.active
            if runtime is not None:
                active += int(runtime.execution is not None and not runtime.execution.done())
                queued += runtime.queue.qsize()
        return {"active_runs": active, "queued_requests": queued, "unfinished_work": unfinished}

    def _interaction_changed(self, run, views):
        """영속 상태가 원본이다. 트랜잭션 확정 이후에만 UI 알림을 예약한다."""
        if self._loop is None or self._loop.is_closed():
            return
        from copy import deepcopy
        run, values = deepcopy(run), [v.to_dict() for v in views]
        def schedule():
            async def publish():
                event = EngineEvent(EngineEventType.INTERACTION_CHANGED, metadata={"interactions": values})
                from llm.services.runtime.runs import RunEventPublisher
                await RunEventPublisher(self.on_event).publish(run, event)
                await self.events.publish("engine", run, event)
            pending = asyncio.create_task(publish())
            self._observation_tasks.add(pending)
            def finished(pending):
                self._observation_tasks.discard(pending)
                if not pending.cancelled():
                    pending.exception()
            pending.add_done_callback(finished)
        from llm.services.infrastructure.transactions import after_commit
        # 응답/취소/갱신이 rollback되면 알리지 않는다. 저장 transaction ContextVar도
        # 확정 콜백 전에 해제되므로 UI의 새 조회가 종료된 transaction에 참여하지 않는다.
        after_commit(lambda: self._loop.call_soon_threadsafe(schedule))

    def _check_open(self) -> None:
        if os.getpid() != self._pid:
            raise RuntimeError("Create a new LargeLanguageModel in each process")
        if self._closed or (self._closing is not None and not self._storage_context.get()):
            raise RuntimeError("LargeLanguageModel is shut down or shutting down")

    async def _storage_call(self, operation, *args, **kwargs):
        """Admit a complete facade transaction off-loop; drain it on shutdown."""
        self._check_open()
        self._bind_loop()
        from llm.services.infrastructure.processes import ProcessCancellation
        cancellation = ProcessCancellation()

        async def execute():
            token = self._storage_context.set(True)
            try:
                with cancellation.scope(), self._observability.scope():
                    return await self._storage.run(operation, *args, **kwargs)
            finally:
                self._storage_context.reset(token)

        pending = asyncio.create_task(execute())
        self._storage_tasks.add(pending)
        self._storage_processes.add(cancellation)
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            pending.cancel()
            # StorageIO는 durable 트랜잭션을 drain하면서 등록된 inspector를 회수한다.
            await drain_on_cancel(pending)
            raise
        finally:
            self._storage_tasks.discard(pending)
            self._storage_processes.discard(cancellation)

    def _bind_loop(self) -> None:
        if os.getpid() != self._pid:
            raise RuntimeError("Create a new LargeLanguageModel in each process")
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("Use LargeLanguageModel on its original event loop")

    def _manager(self, session: Session) -> RunManager:
        self._check_open()
        self._bind_loop()
        key = (session.project_id, session.id)
        if key in self._stopping:
            raise RuntimeError("Session runtime is shutting down")
        if key not in self._managers:
            self._managers[key] = RunManager(
                self.project_manager.sessions, self.engines, session=session,
                repository=self.run_repository, steps=self.step_manager,
                capabilities=self.project_manager.components,
                on_event=self.on_event, on_run_event=self.on_run_event,
                event_handlers=self.event_handlers, subscriptions=self.events,
                tool_runtime=self.services.tool_runtime, policy_resolver=self.policy_resolver,
                provider_calls=self.provider_calls, output_buffer=self.services.output_buffer,
                observability=self._observability)
        return self._managers[key]

    async def _stop_session(self, session: Session) -> None:
        self._check_open()
        self._bind_loop()
        key = (session.project_id, session.id)
        if key in self._stopping:
            raise RuntimeError("Session runtime is already shutting down")
        manager = self._managers.get(key)
        if manager is None:
            return
        self._stopping.add(key)

        async def stop():
            try:
                await manager.shutdown()
                self._managers.pop(key, None)
            finally:
                self._stopping.discard(key)

        await drain_on_cancel(stop())

    def describe_host(self) -> dict:
        """Host 실행 구현/공유 용량을 조회한다. Project 정책이나 SDK 인증값은 포함하지 않는다."""
        self._check_open()
        return self.services.describe()

    def describe_project_config(self, *, components=None) -> dict:
        """UI용 Project 설정 JSON Schema의 독립 사본을 반환한다.

        components=None은 현재 등록된 모든 컴포넌트, []는 선택 없음이다.
        properties.config에는 ProjectConfig, 그 안의 parameters.components에는
        선택 컴포넌트의 설정 스키마가 있다. type/enum/minimum/description으로
        폼을 만들고 additionalProperties=True인 영역은 추가 JSON 키 입력을 허용한다.
        파일/모델을 읽지 않으며 런타임 함수·실제 인증값을 반환하지 않는다.
        저장된 값과 편집 버전은 await project.adescribe_config()으로 별도 조회한다.
        """
        self._check_open()
        from llm.services.schema import describe_project_config
        return describe_project_config(self, components)

    async def shutdown(self) -> None:
        """모든 Session/I/O를 종료한다. 파일 대기는 보존하고 소유한 메모리 대화는 해제한다."""
        self._bind_loop()
        if self._closed:
            return
        if self._closing is None:
            async def close():
                # Accepted facade I/O must finish before ownership is released.
                for cancellation in tuple(self._storage_processes):
                    cancellation.cancel()
                await asyncio.gather(*tuple(self._storage_tasks), return_exceptions=True)
                # Attempt every shutdown even if one worker reports an error.
                results = await asyncio.gather(
                    *(manager.shutdown() for manager in self._managers.values()),
                    return_exceptions=True)
                await asyncio.gather(*tuple(self._observation_tasks), return_exceptions=True)
                await self.events.close()
                self._closed = True
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
                self._managers.clear()
                self.project_manager.sessions.close_conversations(when_idle=True)
            self._closing = asyncio.create_task(close())
        await drain_on_cancel(self._closing)

    async def __aenter__(self) -> "LargeLanguageModel":
        self._check_open()
        self._bind_loop()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.shutdown()


ADD_TOOL_SOURCE = '\n'.join([
    'from llm.components.tools import tool',
    '@tool(effect="read_only")',
    'async def main(a: float, b: float) -> dict:',
    '    """Add two numbers."""',
    '    return {"result": a + b}',
    '',
])


def print_event(run: Run, event: EngineEvent) -> None:
    if event.type == EngineEventType.TEXT_DELTA and event.delta.visibility == "user":
        print(event.delta.text, end="", flush=True)


async def run_request(args: argparse.Namespace) -> int:
    async with LargeLanguageModel(
        args.workspace, components=[ToolComponent()], on_event=print_event,
        conversation_storage=args.conversation_storage,
        engines={"loop": LoopEngine()},
    ) as backend:
        project = await backend.projects.acreate("LoopEngine demo", config=ProjectConfig(parameters={"engines": {"loop": {
            'policy': {key: value for key, value in {'max_iterations': args.max_iterations, 'request_timeout': args.timeout}.items() if value is not None},
            'config': {'completion': {key: value for key, value in {'model': args.model, 'temperature': args.temperature, 'api_base': args.api_base}.items() if value is not None}}}}}),
            components=["tools"] if args.with_tools else [])
        session = await project.sessions.acreate("Streaming request")
        if args.with_tools:
            selected_tools = await project.components.aget("tools")
            await selected_tools.acreate({"source": ADD_TOOL_SOURCE}, identifier="add")
            await selected_tools.aprepare("add")
            await selected_tools.aenable("add")
        print(f"Project: {project.paths.root.resolve()}", file=sys.stderr)
        request = await session.run.submit(args.prompt, engine=args.engine)
        handle = await request.wait()
        run = await handle.aget_data()
        print()
        if run.status != RunStatus.COMPLETED:
            print(f"Run {run.status}: {run.error or 'interrupted'}", file=sys.stderr)
            return 1
        return 0


def main(*argv: str) -> None:
    """Run in an ish tool worker, with explicit arguments and a process exit code.

    Each invocation owns a fresh Project/Session and closes its RunManager. Hosts
    already running an event loop should use the async service APIs instead.
    """
    require_linux()
    parser = argparse.ArgumentParser(prog="llm", description=__doc__)
    parser.add_argument("--engine", required=True, choices=("loop",),
                        help="Execution Engine (the command currently registers loop)")
    parser.add_argument("--model", required=True, help="LiteLLM provider/model identifier")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--conversation-storage", choices=("file", "memory"), default="file",
                        help="Message storage; memory is lost when this command exits")
    parser.add_argument("--api-base", help="Optional OpenAI-compatible endpoint URL")
    parser.add_argument("--temperature", type=float, default=None, help="Omit for model default")
    parser.add_argument("--max-iterations", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--with-tools", action="store_true", help="Register the example add tool")
    args = parser.parse_args(list(argv))
    try:
        code = asyncio.run(run_request(args))
    except KeyboardInterrupt:
        code = 130
    # ish's worker ignores callable return values, so report failure by exit code.
    raise SystemExit(code)


if __name__ == "__main__":
    main(*sys.argv[1:])
