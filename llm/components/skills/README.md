# Skills — 재사용할 지침과 참고 자료

특정 작업에 사용할 instructions와 선택적인 리소스 설명을 열린 JSON 레코드로 저장합니다. Skill 자체가 별도 실행기나 Python 함수 패키지는 아닙니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | SkillComponent 공개 import입니다. |
| [component.py](component.py) | instructions를 필수로 하는 정의 schema와 skills capability를 선언합니다. |

## 사용 예

```python
skills = await project.components.aget("skills")
await skills.acreate({
    "instructions": "변경 전후 동작을 비교하고 근거를 함께 제시하세요.",
    "description": "코드 검토 지침",
}, identifier="review-guide")
```

저장 위치는 `<project>/skills/records/<id>.json`입니다. Agent의 `resources.skills`에 ID를 나열해 연결합니다. 소비하는 Engine/Agent가 지침을 적용하며, 저장만으로 모든 모델 호출에 자동 주입되지 않습니다.

`resources`의 추가 JSON을 실제 파일/네트워크 작업으로 해석하려면 소비자 구현이 필요합니다. 공통 CRUD·설정은 [Component 계약](../README.md)을 따릅니다.

[상위 안내](../README.md)
