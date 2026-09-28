# 메인 도메인 성능·통합 검사

Linux Python **3.12.14**에서 플러그인 폴더의 부모 경로를 import 경로로 둔 뒤 실행한다.
모델 API 키와 네트워크는 필요 없다. 기존 Project를 수정하지 않고 `/tmp` 아래에
독립 작업 디렉토리를 만들며, 보고서에 경로를 남긴다.

```bash
python3.12 -m llm.examples.domain_performance > domain-performance.json
```

기본 부하는 256자 델타 10,000개, 대화 목록 100,000개, 동시 Task 5개,
각 Graph의 작업 노드 8개다. 시간은 추적기를 끈 3회 실행의 중앙값이며,
Python 할당 최고치는 `tracemalloc`으로 별도 실행하여 측정한다. 프로세스 RSS나
모델 지연 시간과는 다르다. 성능 비교 때는 다른 검사와 동시에 실행하지 않는다.

```bash
python3.12 -m llm.examples.domain_performance \
  --deltas 20000 --chunk-chars 256 --history-size 200000 \
  --tasks 5 --graph-nodes 16 --repeats 5 > domain-performance-large.json
```

| 보고서 항목 | 측정 범위 |
| --- | --- |
| stream_projection | Run의 델타 소유권·순서 검증과 본문 누적. 디스크 쓰기 제외 |
| journal_projection | 합성 출력 JSONL 전체를 읽고 ID별 결과 복원 |
| conversation_append | 메모리 Conversation의 델타 추가와 본문 조회 |
| conversation_replay | 합성 Conversation JSONL에서 본문 복원 |
| latest_history_page | 메모리에 적재된 목록의 최신 10건 선택. 파일 목록 탐색 비용 제외 |
| integration | Project/Task 생성, Graph 동시 실행, Run/Step 결과 조회, 백엔드 종료 후 재개방 |

통합 검사는 기본 파일 저장 정책과 트랜잭션을 사용한다. 각 작업의 결과 값과 Step 상태를
확인하며, 검증 실패 시 종료 코드가 0이 아니다. `--skip-integration`은 미세 측정만 실행한다.
이 스크립트는 RAG 품질·실제 모델 호출·3시간 운영·2GB 문서 검색의 검증을 대신하지 않는다.
큰 부하는 임시 디스크와 메모리를 소비하므로 보고서의 작업 경로를 확인해 보관·정리한다.
