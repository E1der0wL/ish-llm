"""프로젝트 정책의 JSON 기본값·스키마·검증. 서비스나 실행 객체에는 의존하지 않는다."""

from copy import deepcopy
import json
from jsonschema import Draft202012Validator


def policy_schema() -> dict:
    """UI용 필드·기본값·설명을 포함한 독립 JSON Schema를 만든다."""
    def field(kind, default, description, **constraints):
        return {"type": kind, "default": default, "description": description, **constraints}
    def section(properties):
        return {"type": "object", "additionalProperties": False, "properties": properties}
    return {"type": "object", "additionalProperties": True, "properties": {
        "approval": section({
            "enabled": field("boolean", False, "호스트가 위임한 요청에만 프로젝트 자동 승인 규칙 적용"),
            "rules": field("array", [], "카테고리·위험도별 승인 규칙; 불확실한 재실행은 제외", items={
                "type": "object", "additionalProperties": False, "required": ["id", "category", "max_risk"],
                "properties": {"id": {"type": "string", "minLength": 1},
                    "category": {"type": "string", "minLength": 1},
                    "max_risk": {"type": "string", "enum": ["low", "medium", "high"]}}})}),
        "context": section({
            "mode": field("string", "full", "엔진에 전달할 과거 대화 선택 방식",
                          enum=["full", "recent", "completed", "recent_completed", "budget"]),
            "max_turns": field("integer", 10, "recent 계열의 과거 턴 수; 현재 입력은 별도", minimum=1),
            "max_chars": field(["integer", "null"], None, "현재 입력 포함 대화 본문 문자 상한", minimum=1)}),
        "completion": section({
            "max_tokens": field(["integer", "null"], None, "요청 토큰 상한; null은 제한하지 않음", minimum=1),
            "reserve_tokens": field("integer", 0, "입력 예산에서 제외할 출력 여유; 출력 길이 제한은 별도", minimum=0),
            "counter": field("string", "model_default", "백엔드에 등록한 요청 토큰 계산기 이름", minLength=1)}),
        "output": section({
            "batch_size": field(["integer", "null"], None, "델타 저장 묶음 크기; null은 백엔드 기본값", minimum=1),
            "max_delay": field(["number", "null"], None, "델타 저장 최대 대기(초); null은 백엔드 기본값", exclusiveMinimum=0),
            "max_chars": field(["integer", "null"], None, "델타 저장 묶음 문자 상한; null은 백엔드 기본값", minimum=1)}),
        "tool_retry": section({
            "max_retries": field(["integer", "null"], None, "조건부 Tool 재시도; null은 호스트 기본값", minimum=0),
            "delay_seconds": field(["number", "null"], None, "Tool 재시도 대기; null은 호스트 기본값", minimum=0)}),
        "provider_retry": section({
            "max_retries": field("integer", 0, "응답 전 429/502/503/504 오류 재시도 횟수", minimum=0),
            "delay_seconds": field("number", 1.0, "최초 재시도 대기 시간", minimum=0),
            "max_delay_seconds": field("number", 30.0, "지수 증가 대기 시간 상한", minimum=0)}),
        "usage": section({
            "max_calls": field(["integer", "null"], None, "Run의 누적 모델 호출 상한", minimum=1),
            "max_tokens": field(["integer", "null"], None, "Run 토큰 예약 상한", minimum=1),
            "project_max_calls": field(["integer", "null"], None, "Project 기간 내 모델 호출 상한", minimum=1),
            "project_max_tokens": field(["integer", "null"], None, "Project 기간 내 토큰 예약 상한", minimum=1),
            "period_seconds": field(["number", "null"], None, "Project 사용량 집계 기간; null은 전체", exclusiveMinimum=0)}),
        "retention": section({
            "unit": field("string", "session", "정리 단위. run은 Session을 유지한다", enum=["session", "run"]),
            "keep_runs": field("integer", 1, "run 정리에서 Session별 최근 완료 Run 보존 개수", minimum=0),
            "max_age_seconds": field(["number", "null"], None, "선택한 정리 단위(Session/Run)의 보관 기간", exclusiveMinimum=0),
            "max_bytes": field(["integer", "null"], None, "정리 대상 기록 용량; Run 단위에서는 대화 크기 추정 포함", minimum=1),
            "max_tokens": field(["integer", "null"], None, "보관된 대화 본문의 토큰 상한", minimum=1),
            "counter": field("string", "model_default", "보관 토큰 계산기 이름", minLength=1)}),
        "run": section({
            "max_queued": field(["integer", "null"], None, "Session별 대기 요청 수 상한", minimum=1),
            "timeout_seconds": field(["number", "null"], None, "문맥 준비 포함 Run 기한(초)", exclusiveMinimum=0),
            "max_capability_rounds": field("integer", 32, "Engine capability 확장 탐색 횟수", minimum=1)})}}


def normalize_policies(value: dict) -> dict:
    """열린 확장 영역은 보존하고 알려진 정책의 생략값을 고정 기본값으로 채운다."""
    schema = policy_schema()
    json.dumps(value, allow_nan=False)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.path)
        raise ValueError(f"Invalid project policy {path}: {error.message}")
    result = deepcopy(value)
    for name, section in schema["properties"].items():
        result[name] = {**{key: field["default"] for key, field in section["properties"].items()},
                        **result.get(name, {})}
        for key, spec in section["properties"].items():
            kind = spec["type"]
            if (kind == "integer" or isinstance(kind, list) and "integer" in kind):
                number = result[name][key]
                if number is not None and type(number) is not int:
                    raise ValueError(f"Policy {name}.{key} requires an integer")
    rule_ids = [rule["id"] for rule in result["approval"]["rules"]]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("Approval rule IDs must be unique")
    context, completion = result["context"], result["completion"]
    if context["mode"] == "budget" and context["max_chars"] is None:
        raise ValueError("Context budget mode requires max_chars")
    if not completion["counter"].strip():
        raise ValueError("Completion counter must be a nonempty name")
    maximum, reserve = completion["max_tokens"], completion["reserve_tokens"]
    if (maximum is None and reserve) or (maximum is not None and reserve >= maximum):
        raise ValueError("Completion reserve_tokens requires max_tokens and must be smaller")
    return result
