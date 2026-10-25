"""Hub가 소유하는 위험도 표시와 명시적 Project 승인 프리셋.

Backend는 이 이름/구간을 알지 않는다. 기본 모델 평가와 명시적 host 분류기의
척도를 구분하며, 모델 평가가 없으면 unknown으로 남긴다.
"""

from hub.locales.keys import MessageKey
from llm.core.interactions import InteractionRequestObservation
from llm.services.runtime.tool_approval_records import ToolApprovalActionView
from llm.core.policies import ApprovalPolicy
from ..tools.advice import RiskAssessment

RISK_SCHEME = "hub-risk-v1"


class ApprovalReview:
    TARGET_FIELDS = frozenset(
        (
            "path",
            "source",
            "destination",
            "cwd",
            "command",
            "argv",
            "workspace_id",
            "url",
        )
    )
    FIELD_SEPARATOR = ":"
    FIELD_NAME_POSITION = 0
    TOOL_LIMIT = 160
    DESCRIPTION_LIMIT = 240
    ELLIPSIS = "…"
    EMPTY_TEXT = ""
    ARGUMENT_LIMIT = 12
    PREVIEW_STRING_LIMIT = 240
    PREVIEW_SEQUENCE_LIMIT = 8
    TARGET_SEPARATOR = FIELD_SEPARATOR + " "
    SCALAR_SIZE = 1
    NAME_LIMIT = 80
    FIRST_LINE = 0
    DETAIL_START = 1
    PARAGRAPH_SEPARATOR = "\n\n"
    JSON_ASCII = False
    JSON_INDENT = 2
    BACKTICK = "`"
    FENCE_MINIMUM = 3
    BACKTICK_RUN = r"`+"
    NO_BACKTICKS = 0
    FENCE_EXTRA = 1
    JSON_LANGUAGE = "json"
    LINE_SEPARATOR = "\n"
    ABSENT_RISK = None
    RISK_SEPARATOR = FIELD_SEPARATOR + " "
    SCORE_OPEN = " ("
    SCORE_CLOSE = ")"
    MIN_RISK = 0
    INVALID_RISK = "max_risk must be a nonnegative integer"

    @classmethod
    def target_argument(cls, key):
        return key in cls.TARGET_FIELDS

    @classmethod
    def target_line(cls, line):
        return cls.target_argument(
            line.partition(cls.FIELD_SEPARATOR)[cls.FIELD_NAME_POSITION]
        )


def review_lines(request, t):
    """Bounded, plain action details shared by the composer and Markdown review."""
    action = InteractionRequestObservation(request).required_action
    lines = [
        t(
            MessageKey.APPROVAL_TOOL,
            name=str(ToolApprovalActionView(action).tool_or_empty)[
                : ApprovalReview.TOOL_LIMIT
            ],
        )
    ]
    if InteractionRequestObservation(request).description:
        lines.append(
            InteractionRequestObservation(request).required_description[
                : ApprovalReview.DESCRIPTION_LIMIT
            ]
            + (
                ApprovalReview.ELLIPSIS
                if len(InteractionRequestObservation(request).required_description)
                > ApprovalReview.DESCRIPTION_LIMIT
                else ApprovalReview.EMPTY_TEXT
            )
        )
    lines.append(
        t(
            MessageKey.APPROVAL_RISK,
            value=risk_label(
                InteractionRequestObservation(request).risk_scheme,
                InteractionRequestObservation(request).risk,
            ),
        )
    )
    from ..tools.advice import FIELD, MODEL_RISK_SCHEME, model_assessment

    assessment = model_assessment(request)
    if assessment:
        lines.append(
            t(
                MessageKey.APPROVAL_MODEL_REASON,
                reason=RiskAssessment.reason(assessment),
            )
            if InteractionRequestObservation(request).risk_scheme == MODEL_RISK_SCHEME
            else t(
                MessageKey.APPROVAL_MODEL_RISK,
                level=RiskAssessment.level(assessment),
                reason=RiskAssessment.reason(assessment),
            )
        )
    arguments = ToolApprovalActionView(action).arguments_or_empty
    targets = []
    if isinstance(arguments, dict):
        for key, value in list(arguments.items())[: ApprovalReview.ARGUMENT_LIMIT]:
            if key == FIELD:
                continue
            if ApprovalReview.target_argument(key):
                # Keep operation targets visible, but never dump file bodies.
                import reprlib

                preview = reprlib.Repr()
                preview.maxstring, preview.maxlist, preview.maxtuple = (
                    ApprovalReview.PREVIEW_STRING_LIMIT,
                    ApprovalReview.PREVIEW_SEQUENCE_LIMIT,
                    ApprovalReview.PREVIEW_SEQUENCE_LIMIT,
                )
                targets.append(
                    f"{key}{ApprovalReview.TARGET_SEPARATOR}{preview.repr(value)}"
                )
            else:
                size = (
                    len(value)
                    if isinstance(value, (str, list, dict))
                    else ApprovalReview.SCALAR_SIZE
                )
                lines.append(
                    t(
                        MessageKey.APPROVAL_ARGUMENT,
                        name=str(key)[: ApprovalReview.NAME_LIMIT],
                        kind=type(value).__name__,
                        size=size,
                    )
                )
        if len(arguments) > ApprovalReview.ARGUMENT_LIMIT:
            lines.append(ApprovalReview.ELLIPSIS)
    return [
        lines[ApprovalReview.FIRST_LINE],
        *targets,
        *lines[ApprovalReview.DETAIL_START :],
    ]


def review_text(request):
    """Prepare the complete review payload before it reaches the UI thread."""
    import json
    import re

    if not InteractionRequestObservation(request).action:
        return (
            InteractionRequestObservation(request).title
            + ApprovalReview.PARAGRAPH_SEPARATOR
            + InteractionRequestObservation(request).description_or_empty
        )
    action = json.dumps(
        InteractionRequestObservation(request).required_action,
        ensure_ascii=ApprovalReview.JSON_ASCII,
        indent=ApprovalReview.JSON_INDENT,
    )
    fence = ApprovalReview.BACKTICK * max(
        ApprovalReview.FENCE_MINIMUM,
        max(
            (len(part) for part in re.findall(ApprovalReview.BACKTICK_RUN, action)),
            default=ApprovalReview.NO_BACKTICKS,
        )
        + ApprovalReview.FENCE_EXTRA,
    )
    return (
        InteractionRequestObservation(request).title
        + ApprovalReview.PARAGRAPH_SEPARATOR
        + InteractionRequestObservation(request).description_or_empty
        + f"{ApprovalReview.PARAGRAPH_SEPARATOR}{fence}{ApprovalReview.JSON_LANGUAGE}{ApprovalReview.LINE_SEPARATOR}{action}{ApprovalReview.LINE_SEPARATOR}{fence}"
    )


def risk_label(scheme, risk):
    from ..tools.advice import MODEL_RISK_SCHEME

    if risk is ApprovalReview.ABSENT_RISK:
        return RiskAssessment.UNKNOWN
    if scheme not in (RISK_SCHEME, MODEL_RISK_SCHEME):
        return f"{scheme}{ApprovalReview.RISK_SEPARATOR}{risk}"
    return (
        RiskAssessment.LOW
        if risk <= RiskAssessment.SCORES[RiskAssessment.LOW]
        else (
            RiskAssessment.MEDIUM
            if risk <= RiskAssessment.SCORES[RiskAssessment.MEDIUM]
            else RiskAssessment.HIGH
        )
    ) + f"{ApprovalReview.SCORE_OPEN}{risk}{ApprovalReview.SCORE_CLOSE}"


def approval_preset(*, max_risk, categories):
    """사용자가 선택한 threshold만 Project JSON으로 반환한다. 자동 적용하지 않는다."""
    if type(max_risk) is not int or max_risk < ApprovalReview.MIN_RISK:
        raise ValueError(ApprovalReview.INVALID_RISK)
    return ApprovalPolicy.selected_threshold(RISK_SCHEME, max_risk, categories)
