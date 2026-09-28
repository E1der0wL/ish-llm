"""프로젝트별 확장 데이터와 선택적 런타임 capability를 제공한다.

Project-owned component data and optional runtime capabilities."""

__all__ = ["Component", "ProjectComponent", "ComponentRegistry"]


def __getattr__(name):
    # 임베딩만 import할 때 서비스·도메인·DB를 초기화하지 않는다.
    if name in ("Component", "ProjectComponent"):
        from . import base
        return getattr(base, name)
    if name == "ComponentRegistry":
        from .registry import ComponentRegistry
        return ComponentRegistry
    raise AttributeError(name)
