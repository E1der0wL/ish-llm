"""Hub가 소유하는 위험도 표시와 명시적 Project 승인 프리셋.

Backend는 이 이름/구간을 알지 않는다. shell 문자열의 안전성을 추정하지 않으며
신뢰한 classifier가 없다면 unknown으로 남겨 사용자 승인을 요구한다.
"""

RISK_SCHEME = "hub-risk-v1"


def review_summary(request, t):
    """A bounded action card; the complete immutable request is opt-in."""
    action = request["action"]
    lines = [t("approval_tool", name=str(action.get("tool", ""))[:160])]
    if request.get("description"):
        lines.append(request["description"][:240] + ("…" if len(request["description"]) > 240 else ""))
    lines.append(t("approval_risk", value=risk_label(request.get("risk_scheme"), request.get("risk"))))
    arguments = action.get("arguments", {})
    if isinstance(arguments, dict):
        for key, value in list(arguments.items())[:12]:
            if key in {"path", "source", "destination", "cwd", "command", "argv", "workspace_id", "url"}:
                # Keep operation targets visible, but never dump file bodies.
                import reprlib
                preview = reprlib.Repr()
                preview.maxstring, preview.maxlist, preview.maxtuple = 240, 8, 8
                lines.append(f"{key}: {preview.repr(value)}")
            else:
                size = len(value) if isinstance(value, (str, list, dict)) else 1
                lines.append(t("approval_argument", name=str(key)[:80], kind=type(value).__name__, size=size))
        if len(arguments) > 12:
            lines.append("…")
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
    if risk is None:
        return "unknown"
    if scheme != RISK_SCHEME:
        return f"{scheme}: {risk}"
    return ("low" if risk <= 20 else "medium" if risk <= 60 else "high") + f" ({risk})"


def approval_preset(*, max_risk, categories):
    """사용자가 선택한 threshold만 Project JSON으로 반환한다. 자동 적용하지 않는다."""
    if type(max_risk) is not int or max_risk < 0:
        raise ValueError("max_risk must be a nonnegative integer")
    return {"enabled": True, "risk_scheme": RISK_SCHEME, "rules": [
        {"id": category, "category": category, "max_risk": max_risk} for category in categories]}
