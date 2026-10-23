"""MCP 연결 정의를 관리하는 MCPComponent를 공개한다."""

__all__ = ["MCPComponent"]


def __getattr__(name):
    if name in __all__:
        from . import component
        return getattr(component, name)
    raise AttributeError(name)
