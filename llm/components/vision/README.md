# Vision — 이미지 전처리·OCR·모델 해석

VisionComponent는 Project에 등록한 PNG/JPEG 이미지와 가공본을 관리합니다.
Pillow는 전처리, OCR backend는 문자·좌표 추출, LiteLLM은 이미지 해석을 담당합니다.
Engine이나 별도의 실행 도메인이 아니며, Tool로 호출하면 기존 ToolExecutor의 승인과
Run/Step 기록을 사용합니다. 직접 Component API를 호출하면 새 Run은 만들지 않습니다.

## 시작하기

Linux Python 3.12.14, Pillow가 필요합니다. ish는 PLUGIN_META의 `Pillow|PIL`을 설치합니다.
Tesseract 사용 시 호스트에 `tesseract` 실행 파일과 사용할 언어 데이터를 별도로 설치하세요.
컴포넌트는 시스템 패키지나 OCR 모델을 자동 설치하지 않습니다.

```python
from llm.llm import LargeLanguageModel, ProjectConfig, VisionComponent

async with LargeLanguageModel(workspace, components=[VisionComponent()]) as backend:
    project = await backend.projects.acreate("Images", components=["vision"],
        config=ProjectConfig(parameters={"components": {"vision": {'config': {'ocr': {'backend': 'tesseract', 'backends': {'tesseract': {'language': 'eng', 'page_segmentation': 6}}}}}}}))
    vision = await project.components.aget("vision")
    image = await vision.aimport_image("/path/screenshot.png", title="Error screen")
    derived = await vision.apreprocess(image["id"], operations=[
        {"operation": "crop", "box": [0, 0, 400, 200]},
        {"operation": "resize", "scale": 2},
        {"operation": "grayscale"},
    ])
    result = await vision.aocr(derived["id"])
    print(result["text"], result["blocks"])
```

예제의 crop 범위는 실제 이미지 안에 있어야 합니다. 모든 연산의 좌표는 직전 결과를
기준으로 합니다. `box=[left, top, right, bottom]`, rotate는 반시계 방향 90/180/270도입니다.
지원 연산은 crop, resize(scale), rotate(degrees), grayscale, exif_transpose,
contrast(factor), sharpen(factor), format(PNG/JPEG)입니다. format은 마지막에만 지정합니다.
가공본은 손실 없는 PNG 표현으로 저장하고, JPEG를 명시하면 Pillow의 native 인코딩을
사용합니다. 알파 채널을 자동으로 버리지 않으며 원본 바이트는 변경하지 않습니다.

## 설정 계약

설정 원본은 `ProjectConfig.parameters.components.vision` 하나입니다.
`vision.aconfigure()`도 그 설정을 교체합니다. 빈 설정은 `{}`로 유지됩니다.

| 항목 | 미설정 동작 |
| --- | --- |
| `config.ocr.backend` | `aocr(backend=...)`가 없으면 설정 오류. 등록된 Tesseract를 자동 선택하지 않음 |
| `config.ocr.backends.<이름>` | backend에 빈 options 전달. Tesseract language/psm 등은 native 동작 |
| `policy.ocr.timeout` | 추가 deadline 없음. 명시적 null도 무제한 |
| `config.completion` | VLM 사용 시 model 필수. timeout/temperature/token/retry 값을 추가하지 않음 |
| `policy.provider` | 외부 wrapper retry/deadline 없음. 기존 ProviderRuntime 계약 공유 |
| `policy.limits.max_bytes`, `policy.limits.max_pixels` | ish 추가 제한 없음. Pillow 자체 이미지 검증은 유지 |

OCR backend 선택은 **호출 인자 → Project 명시 설정 → 오류** 순서입니다.
`auto`, unknown backend, backend=null은 허용하지 않습니다. 호출의 `options`는 선택한
backend의 Project options 위에 명시된 키만 덮습니다. Tesseract options는 language,
page_segmentation, variables입니다. 실패 시 다른 OCR이나 VLM으로 바꾸지 않습니다.

VLM은 이 API가 하나의 텍스트 해석 결과를 반환하는 계약 때문에 `stream=False`, `n=1`을
강제합니다. 이는 `effective.values`의 사용자 설정을 채우지 않고 `enforced`에 표시합니다.
messages는 입력 이미지와 명시한 prompt로 구성하며 nested Tool은 지원하지 않습니다.

## Tool과 직접 API

선택된 Vision은 tools capability로 다음 Tool을 제공합니다. 임의 파일 경로가 아니라
호스트/UI에서 미리 등록한 `image_id`를 사용합니다.

| Tool | 직접 API | 결과 |
| --- | --- | --- |
| `image_preprocess` | `apreprocess(image_id, operations=...)` | 새 이미지 레코드·원본 및 변환 이력 |
| `image_ocr` | `aocr(image_id, backend=..., options=...)` | text, blocks, backend, 이미지 참조 |
| `image_analyze` | `aanalyze(image_id, prompt=...)` | model_analysis 텍스트·모델·이미지 참조 |

OCR backend 등록이 없으면 image_ocr Tool도 없습니다. Project에 backend가 없으면 Tool
schema에서 backend를 필수 인자로 노출합니다. 승인 전에는 OCR 프로세스나 VLM을 호출하지
않습니다. 설정/backend revision 변경은 이전 Run의 Tool 재개 계약과 충돌하여 거부됩니다.

`aimport_image(path_or_bytes, title=..., identifier=...)`로 등록하고 `aload/alist`로 조회합니다.
`aupdate/asave`는 title/metadata만 변경합니다. 동기 등록은 `import_image()`이며
비동기 UI는 `aimport_image()`를 사용합니다. 공통 JSON create는 이미지 등록에 사용하지 않습니다.

VLM은 `vision.config.completion.model` 등 모델 서버 설정 후 호출합니다. `aanalyze()`는 기존 provider
admission·재시도·사용량 관찰을 사용합니다. OCR 결과의 confidence는 backend 고유 척도이며
다른 backend의 점수와 직접 비교하지 않습니다. OCR 좌표는 처리한 이미지의 픽셀 좌표입니다.
VLM 해석을 검증된 OCR이나 실제 OS 상태로 간주하지 마세요.

## Completion과 RAG 연결

```python
# custom completion 호출의 user content 배열에 추가할 수 있는 LiteLLM 이미지 block
block = await vision.acompletion_content(image_id)

# RAG는 기존 문서 API와 명시된 embedding/extraction 설정을 그대로 사용
document = await vision.aextract_document(image_id, mode="ocr", title="Screen text")
await project.components.rag.aadd_document(**document, identifier="screen-manual")

# 모델 해석을 문서로 만들려면 별도로 명시
document = await vision.aextract_document(image_id, mode="analysis",
    title="Screen analysis", prompt="Describe visible errors and distinguish uncertainty.")
```

문서 metadata에는 derived_text 표시, 이미지 ID/hash, Project ID, OCR 좌표 또는 모델 출처가
들어갑니다. RAG indexing/publish는 RAG가 소유합니다. OCR이 빈 문자열을 반환하는 것은
정상일 수 있지만 빈 문서를 RAG 입력으로 생성하지는 않습니다. 이미지 등록·삭제가 기존
RAG 문서를 자동 생성·수정·삭제하지 않습니다. 연결 문서의 수명은 호출자가 관리합니다.

현재 Session 메시지는 텍스트 계약을 유지합니다. UI 이미지 첨부를 자동 변환하지 않으며
Tool 사용 시 등록된 ID를 요청에 전달하거나 custom completion의 content block을 사용합니다.
Base64 block은 전송용이며 로그·Conversation에 그대로 저장하지 마세요. 이미지 원문은
컴포넌트 asset에만 저장하고 Tool 결과와 모델 사용량 기록에는 넣지 않습니다.

## 저장·취소·확장

```text
<project>/vision/
  records/<image_id>.json    # format_version=1, hash, MIME/크기, title/metadata, origin
  assets/<sha256>.bin       # 등록한 원본 또는 가공본 바이트
  usage/                   # 공통 Component 모델 사용량 관찰 기록
```

준비/전처리는 잠금 밖에서 수행하고 등록 확정은 workspace 잠금과 공통 atomic transaction을
사용합니다. 도중 설정 변경·이미지 삭제·내용 손상을 확인하면 확정하지 않습니다.
`adelete()`는 tombstone을 남겨 이후 읽기/실행을 막습니다. 기존 Step/RAG 출처 때문에
이미지 ID와 bytes는 보존하며 ID를 재사용하지 않습니다. **이 API는 디스크 용량을 회수하지
않습니다.** 물리 정리는 Component/Project 영구 삭제로 수행하며 개별 asset GC는 미구현입니다.
Clone/backup은 tombstone과 asset도 함께 유지합니다.

Tesseract 취소 시 Linux 프로세스 그룹을 종료하고 pipe를 회수합니다. 이는 자원 회수이며
파일/네트워크 sandbox가 아닙니다. Pillow 전처리 thread는 즉시 강제 종료할 수 없지만,
취소된 호출은 그 결과를 등록하지 않습니다. 원자적 등록 확정이 시작된 뒤의 취소는 기존
StorageIO의 commit 완료 계약을 따릅니다.

새 OCR은 `OCRBackend`의 revision, configuration_schema(), async recognize(bytes, options=...)
계약을 구현한 뒤 `VisionComponent(ocr_backends={"custom": backend})`에 등록합니다.
결과는 text와 blocks를 포함하는 JSON이며 각 block은 text와 유효한 box를 가져야 합니다.
confidence는 선택 사항이며 유한한 수여야 합니다. backend가 직접 취소를 지원해야 합니다.
OCR 구현이나 실행 환경을 바꾸면 해당 backend.revision을 갱신하세요. 호스트가
completion_fn 등 Vision 구현을 교체하면 VisionComponent(revision=...)도 갱신해
이전 승인·체크포인트에서 달라진 구현을 실행하지 않도록 합니다.
PaddleOCR/EasyOCR 구현 및 자동 선택은 아직 포함하지 않습니다.

| 파일 | 역할 |
| --- | --- |
| [component.py](component.py) | 설정·레코드·asset 저장, 복제, capability 등록 |
| [data.py](data.py) | UI/Tool/RAG가 공유하는 수명 검사 API |
| [images.py](images.py) | Pillow 검증·전처리. 저장/모델 호출 없음 |
| [backends.py](backends.py) | OCR 계약·Tesseract·LiteLLM 분석과 결과 검증 |
| [tools.py](tools.py) | 공통 API를 사용하는 세 Tool 어댑터 |
| [__init__.py](__init__.py) | 공개 import |

실행 가능한 예제는 [vision.md](../../../examples/llm/vision.md)를 참고하세요.
