"""Hub가 소유하는 위험도 표시와 명시적 Project 승인 프리셋.

Backend는 이 이름/구간을 알지 않는다. 기본 모델 평가와 명시적 host 분류기의
척도를 구분하며, 모델 평가가 없으면 unknown으로 남긴다.
"""

RISK_SCHEME = "hub-risk-v1"


def review_lines(request, t):
    """Bounded, plain action details shared by the composer and Markdown review."""
    action = request["action"]
    lines = [t("approval_tool", name=str(action.get("tool", ""))[:160])]
    if request.get("description"):
        lines.append(request["description"][:240] + ("…" if len(request["description"]) > 240 else ""))
    lines.append(t("approval_risk", value=risk_label(request.get("risk_scheme"), request.get("risk"))))
    from .tool_advice import FIELD, MODEL_RISK_SCHEME, model_assessment
    assessment = model_assessment(request)
    if assessment:
        lines.append(t("approval_model_reason", reason=assessment[1])
            if request.get("risk_scheme") == MODEL_RISK_SCHEME
            else t("approval_model_risk", level=assessment[0], reason=assessment[1]))
    arguments = action.get("arguments", {})
    targets = []
    if isinstance(arguments, dict):
        for key, value in list(arguments.items())[:12]:
            if key == FIELD:
                continue
            if key in {"path", "source", "destination", "cwd", "command", "argv", "workspace_id", "url"}:
                # Keep operation targets visible, but never dump file bodies.
                import reprlib
                preview = reprlib.Repr()
                preview.maxstring, preview.maxlist, preview.maxtuple = 240, 8, 8
                targets.append(f"{key}: {preview.repr(value)}")
            else:
                size = len(value) if isinstance(value, (str, list, dict)) else 1
                lines.append(t("approval_argument", name=str(key)[:80], kind=type(value).__name__, size=size))
        if len(arguments) > 12:
            lines.append("…")
    return [lines[0], *targets, *lines[1:]]


def review_summary(request, t):
    """A bounded action card; the complete immutable request is opt-in."""
    lines = review_lines(request, t)
    # Fence length must survive arbitrary paths/commands containing backticks.
    import re
    text = "\n".join(lines)
    fence = "`" * max(3, max((len(s) for s in re.findall(r"`+", text)), default=0) + 1)
    return request["title"] + f"\n\n{fence}text\n{text}\n{fence}\n\n" + t("question_action_hint")


def review_text(request):
    """Prepare the complete review payload before it reaches the UI thread."""
    import json
    import re
    action = json.dumps(request["action"], ensure_ascii=False, indent=2)
    fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", action)), default=0) + 1)
    return request["title"] + "\n\n" + request.get("description", "") + f"\n\n{fence}json\n{action}\n{fence}"


def risk_label(scheme, risk):
    from .tool_advice import MODEL_RISK_SCHEME
    if risk is None:
        return "unknown"
    if scheme not in (RISK_SCHEME, MODEL_RISK_SCHEME):
        return f"{scheme}: {risk}"
    return ("low" if risk <= 20 else "medium" if risk <= 60 else "high") + f" ({risk})"


def approval_preset(*, max_risk, categories):
    """사용자가 선택한 threshold만 Project JSON으로 반환한다. 자동 적용하지 않는다."""
    if type(max_risk) is not int or max_risk < 0:
        raise ValueError("max_risk must be a nonnegative integer")
    return {"enabled": True, "risk_scheme": RISK_SCHEME, "rules": [
        {"id": category, "category": category, "max_risk": max_risk} for category in categories]}
