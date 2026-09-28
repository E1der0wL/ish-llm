"""엔진·리소스·입출력 계약·정책을 가진 재사용 업무 정의를 관리한다."""

from .component import AgentComponent
from .data import AgentData

__all__ = ["AgentComponent", "AgentData"]
