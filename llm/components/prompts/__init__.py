"""여러 소비자가 선택해서 사용하는 프롬프트 정의 CRUD."""

__all__ = ["PromptComponent"]


def __getattr__(name):
    if name in __all__:
        from . import component
        return getattr(component, name)
    raise AttributeError(name)
