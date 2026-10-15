"""학습 자료·예제·임시 파일을 명시적으로 보관한다. 데이터의 의미는 Application이 정한다."""

from llm.components.files import FileComponent, FileData, FILE_MANIFEST
from llm.core.schema import object_schema, metadata_schema


class DataComponent(FileComponent):
    """패키지 안의 바이너리/텍스트 파일을 소유한다. 실행 권한이나 자동 보관 정책은 없다."""

    name = "data"
    directory = "data"
    capabilities = ("data",)
    data_class = FileData
    record_name = "data.json"
    schema = object_schema({
        "title": {"type": "string"},
        "description": {"type": "string"},
        "metadata": metadata_schema(),
        "files": FILE_MANIFEST,
    })
