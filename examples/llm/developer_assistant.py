"""소스 수정·문제 분석과 Skill 탐색을 연결하는 명시적 설정 예제. 자동 작업 성공을 보장하지 않는다."""

import argparse
import asyncio
import json
from pathlib import Path

from llm.components.skills import SkillComponent
from llm.components.tools.builtin import BuiltinTools, BuiltinToolComponent
from llm.core.models import ProjectConfig, RunStatus
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel, print_event


def starter_skills():
    """호스트/UI가 선택하여 설치할 예제 지침. 라이브러리의 숨은 시스템 프롬프트가 아니다."""
    return {
        "code-change": {
            "title": "코드 수정과 검증", "description": "소스 탐색, 작은 변경, 실제 검증과 결과 보고",
            "tags": ["code", "programming", "코드", "수정", "테스트"],
            "instructions": (
                "1. 사용자 요청과 저장소 지침을 확인하고 file_list/file_search로 관련 소스를 찾는다.\n"
                "2. file_read로 호출 관계와 현재 코드를 읽고, 관찰된 사실과 수정 가설을 구분한다.\n"
                "3. 가능하면 수정 전 재현/검증 명령을 실행하여 실제 실패를 확인한다.\n"
                "4. 현재 SHA-256으로 필요한 부분만 file_patch한다. 충돌이면 다시 읽고 판단한다.\n"
                "5. 등록된 test_run/check_run 또는 허용된 명령으로 같은 조건을 다시 검사한다.\n"
                "6. 종료 코드와 출력, diff를 확인하여 변경·검증·남은 불확실성을 보고한다.\n"
                "검증에 실패했다면 실패 원인을 확인하고 수정 단계로 돌아간다. 실행하지 않은 검사는 통과했다고 말하지 않는다."
            ),
        },
        "computer-diagnosis": {
            "title": "컴퓨터·프로그램 문제 분석", "description": "재현 조건, 프로세스 상태와 로그를 비교해 원인을 확인",
            "tags": ["debug", "diagnosis", "process", "오류", "분석", "터미널"],
            "instructions": (
                "1. 기대 동작과 실제 현상, 발생 조건을 구분한다. 먼저 읽기 전용 관찰을 한다.\n"
                "2. system_inspect/process_list/process_inspect로 환경과 PID의 실제 상태를 확인한다.\n"
                "3. file_read의 tail_lines와 file_search로 관련 로그 및 입력/출력 경로의 소스를 읽는다.\n"
                "4. process_start로 허용된 최소 재현을 실행하고 process_output의 next offset으로 이어 읽는다.\n"
                "5. 가설마다 반증할 관찰을 정한다. 시각·PID·입력 분기 기록이 없으면 과거 흐름을 확정하지 않는다.\n"
                "6. 근거를 확보한 뒤 필요한 변경과 검증을 수행하고, 사실/가설/추가 관찰 필요를 구분해 보고한다.\n"
                "기존 ish 터미널과 이 Toolkit이 실행한 프로세스는 다르다. pipe 입력은 PTY가 아니다. "
                "process_write는 개행을 자동 추가하지 않으며 결과가 불확실한 입력을 임의 재전송하지 않는다."
            ),
        },
    }


async def install_guides(project):
    """명시적으로 요청했을 때만 설치하며 사용자가 편집한 기존 지침은 유지한다."""
    skills = await project.components.aget("skills")
    existing = await skills.alist()
    for identifier, definition in starter_skills().items():
        if identifier not in existing:
            await skills.acreate(definition, identifier=identifier)


async def run_request(args):
    config = json.loads(args.config.read_text(encoding="utf-8"))
    work, workspace = args.workdir.resolve(), args.workspace.resolve()
    if workspace == work or work in workspace.parents:
        raise ValueError("Place the backend workspace outside the editable workdir")
    async with BuiltinTools(work, **config.get("builtin", {})) as toolkit:
        async with LargeLanguageModel(workspace,
                components=[BuiltinToolComponent(toolkit, name="computer"), SkillComponent()],
                engines={"loop": LoopEngine()}, on_event=print_event) as backend:
            project = (await backend.projects.aload(args.project_id) if args.project_id else
                       await backend.projects.acreate("Developer assistant", components=["computer", "skills"],
                           config=ProjectConfig(config["project_config"])))
            if args.install_guides:
                await install_guides(project)
            session = await project.sessions.acreate("Programming / diagnosis")
            print(f"Project: {project.id}\nSession: {session.id}")
            run = await (await session.run.submit(args.prompt, engine="loop")).wait()
            result = await run.aresult()
            print(f"\nRun: {run.id} / {result.status.value}")
            if result.status != RunStatus.COMPLETED:
                print(result.error_code or result.error or result.status.value)
                return 1
            return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project-id", help="기존 설정·Skill을 사용할 Project ID. project_config는 다시 적용하지 않음")
    parser.add_argument("--install-guides", action="store_true", help="아직 없는 예제 Skill을 명시적으로 추가")
    parser.add_argument("prompt")
    return asyncio.run(run_request(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
