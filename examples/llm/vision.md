# Vision 실제 사용 검사

Linux Python 3.12.14에서 저장소 루트를 기준으로 실행합니다. Pillow와 Tesseract,
영문 언어 데이터가 필요합니다. Python 패키지는 ish의 PLUGIN_META에도 선언되어 있지만
Tesseract 시스템 실행 파일은 호스트에서 설치해야 합니다.

```bash
python -m examples.llm.vision --config examples/llm/vision.config.example.json \
  --workspace /tmp/vision-check --image /path/screenshot.png --mode ocr
```

매번 새 Project와 이미지 레코드를 생성하며 OCR 결과와 저장 위치를 콘솔에 표시합니다.
기본 예제는 모델 서버 호출이 필요 없습니다. 한국어는 호스트에 kor 데이터를 설치한 뒤
설정의 language를 `kor+eng` 등으로 명시하세요. language/psm/크기 상한은 **예제의 명시적
선택**이며 라이브러리 기본값이 아닙니다.

전처리하려면 JSON 배열 파일을 `--operations /path/operations.json`으로 전달합니다.

```json
[{"operation": "resize", "scale": 2}, {"operation": "grayscale"}]
```

VLM 검사는 별도 설정 파일의 `parameters.components.vision.config.completion`에 이미지 지원
모델의 model, api_base, api_key 등을 명시한 뒤 실행합니다. 키는 개인 설정 파일에 보관하고
저장소에 올리지 마세요. provider timeout/retry가 필요하면 같은 vision.policy.provider에 지정합니다.

```bash
python -m examples.llm.vision --config /path/private-vision.json \
  --workspace /tmp/vision-check --image /path/screenshot.png \
  --mode analysis --prompt '화면에 보이는 오류와 추정 원인을 구분해 설명해줘.'
```

이 예제는 Component 직접 호출입니다. Loop/Graph Tool 실행과 RAG 등록 방법, 삭제·보존
계약은 [Vision README](../../llm/components/vision/README.md)를 참고하세요.
`tests.llm.test_vision`은 실제 Tesseract와 fake model을 구분해서 검사합니다.
