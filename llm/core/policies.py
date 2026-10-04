"""프로젝트의 명시적 JSON 정책 스키마·검증. 서비스나 실행 객체에는 의존하지 않는다."""

from copy import deepcopy
import json
from jsonschema import Draft202012Validator


def policy_schema() -> dict:
    """UI용 필드·설명을 포함한 독립 JSON Schema를 만든다."""
    def field(kind, description, **constraints):
        return {"type": kind, "description": description, **constraints}
    def section(properties):
        return {"type": "object", "additionalProperties": False, "properties": properties}
    return {"type": "object", "additionalProperties": True, "properties": {
        "approval": section({
            "enabled": field("boolean", "호스트가 위임한 요청에만 프로젝트 자동 승인 규칙 적용"),
            "rules": field("array", "카테고리·위험도별 승인 규칙; 불확실한 재실행은 제외", items={
                "type": "object", "additionalProperties": False, "required": ["id", "category", "max_risk"],
                "properties": {"id": {"type": "string", "minLength": 1},
                    "category": {"type": "string", "minLength": 1},
                    "max_risk": {"type": "string", "enum": ["low", "medium", "high"]}}})}),
        "context": section({
            "mode": field("string", "엔진에 전달할 과거 대화 선택 방식",
                          enum=["full", "recent", "completed", "recent_completed", "budget"]),
            "max_turns": field("integer", "recent 계열의 과거 턴 수; 현재 입력은 별도", minimum=1),
            "max_chars": field(["integer", "null"], "현재 입력 포함 대화 본문 문자 상한", minimum=1)}),
        "completion": section({
            "max_tokens": field(["integer", "null"], "요청 토큰 상한; null은 제한하지 않음", minimum=1),
            "reserve_tokens": field("integer", "입력 예산에서 제외할 출력 여유; 출력 길이 제한은 별도", minimum=0),
            "counter": field("string", "백엔드에 등록한 요청 토큰 계산기 이름", minLength=1)}),
        "output": section({
            "batch_size": field(["integer", "null"], "델타 저장 묶음 크기; null은 백엔드 기본값", minimum=1),
            "max_delay": field(["number", "null"], "델타 저장 최대 대기(초); null은 백엔드 기본값", exclusiveMinimum=0),
            "max_chars": field(["integer", "null"], "델타 저장 묶음 문자 상한; null은 백엔드 기본값", minimum=1)}),
        "tool_retry": section({
            "max_retries": field(["integer", "null"], "조건부 Tool 재시도; null은 비활성화", minimum=0),
            "delay_seconds": field(["number", "null"], "Tool 재시도 대기; null은 비활성화", minimum=0)}),
        "provider_retry": section({
            "max_retries": field("integer", "응답 전 429/502/503/504 오류 재시도 횟수", minimum=0),
            "delay_seconds": field("number", "최초 재시도 대기 시간", minimum=0),
            "max_delay_seconds": field("number", "지수 증가 대기 시간 상한", minimum=0)}),
        "usage": section({
            "max_calls": field(["integer", "null"], "Run의 누적 모델 호출 상한", minimum=1),
            "max_tokens": field(["integer", "null"], "Run 토큰 예약 상한", minimum=1),
            "project_max_calls": field(["integer", "null"], "Project 기간 내 모델 호출 상한", minimum=1),
            "project_max_tokens": field(["integer", "null"], "Project 기간 내 토큰 예약 상한", minimum=1),
            "period_seconds": field(["number", "null"], "Project 사용량 집계 기간; null은 전체", exclusiveMinimum=0)}),
        "retention": section({
            "unit": field("string", "정리 단위. run은 Session을 유지한다", enum=["session", "run"]),
            "keep_runs": field("integer", "run 정리에서 Session별 최근 완료 Run 보존 개수", minimum=0),
            "max_age_seconds": field(["number", "null"], "선택한 정리 단위(Session/Run)의 보관 기간", exclusiveMinimum=0),
            "max_bytes": field(["integer", "null"], "정리 대상 기록 용량; Run 단위에서는 대화 크기 추정 포함", minimum=1),
            "max_tokens": field(["integer", "null"], "보관된 대화 본문의 토큰 상한", minimum=1),
            "counter": field("string", "보관 토큰 계산기 이름", minLength=1),
            "counter_params": field("object", "보관 계산기에 전달할 명시적 인자. messages는 서비스가 소유한다",
                                    **{"not": {"required": ["messages"]}})}),
        "run": section({
            "max_queued": field(["integer", "null"], "Session별 대기 요청 수 상한", minimum=1),
            "timeout_seconds": field(["number", "null"], "문맥 준비 포함 Run 기한(초)", exclusiveMinimum=0),
            "max_capability_rounds": field(["integer", "null"], "Engine capability 확장 탐색 횟수", minimum=1)})}}


def normalize_policies(value: dict) -> dict:
    """명시된 키만 검증한다. 생략한 정책과 leaf는 그대로 생략한다."""
    schema = policy_schema()
    json.dumps(value, allow_nan=False)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.path)
        raise ValueError(f"Invalid project policy {path}: {error.message}")
    result = deepcopy(value)
    # 존재하는 키만 검증·보존한다. missing은 정책 활성화나 기본값 생성을 뜻하지 않는다.
    for name, section in schema["properties"].items():
        for key, spec in section["properties"].items():
            number = result.get(name, {}).get(key)
            kind = spec["type"]
            if (kind == "integer" or isinstance(kind, list) and "integer" in kind):
                if number is not None and type(number) is not int:
                    raise ValueError(f"Policy {name}.{key} requires an integer")
    rule_ids = [rule["id"] for rule in result.get("approval", {}).get("rules", [])]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("Approval rule IDs must be unique")
    context, completion = result.get("context", {}), result.get("completion", {})
    if context.get("mode") == "budget" and context.get("max_chars") is None:
        raise ValueError("Context budget mode requires max_chars")
    if "counter" in completion and not completion["counter"].strip():
        raise ValueError("Completion counter must be a nonempty name")
    maximum, reserve = completion.get("max_tokens"), completion.get("reserve_tokens")
    if (maximum is None and reserve) or (maximum is not None and reserve is not None and reserve >= maximum):
        raise ValueError("Completion reserve_tokens requires max_tokens and must be smaller")
    return result
