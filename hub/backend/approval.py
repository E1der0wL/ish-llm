"""Hub가 소유하는 위험도 표시와 명시적 Project 승인 프리셋.

Backend는 이 이름/구간을 알지 않는다. shell 문자열의 안전성을 추정하지 않으며
신뢰한 classifier가 없다면 unknown으로 남겨 사용자 승인을 요구한다.
"""

RISK_SCHEME = "hub-risk-v1"


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
