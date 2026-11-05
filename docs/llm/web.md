# 웹 검색·조회 어댑터

Backend는 검색 서비스를 선택하지 않는다. Application은 서비스/인증/연결 수명/네트워크
허용 범위/검색 여부/출력량을 정한다. 검색 결과와 HTML은 외부 자료이며 지침·Tool 권한이 아니다.
별도 브라우저 없이 HTTP 텍스트 조회를 할 수 있지만 JavaScript 실행·로그인 UI·PDF/OCR은
이 어댑터의 계약이 아니다. 필요한 경우 Application이 다른 어댑터를 선택한다.

## HTTP 조회

`llm.providers.web.WebFetch`는 Application이 연 `httpx.AsyncClient`를 빌린다.
선택 의존성은 `web` extra다. 요청의 `max_bytes`는 해제된 본문 바이트의 명시적 상한,
`max_chars`는 반환 본문 문자의 명시적 상한이다. 미설정 제한은 추가하지 않는다.
HTTPX의 timeout/redirect/proxy/auth/encoding을 Backend가 복제하거나 덮어쓰지 않는다.
상세 SDK 동작은 [비동기 요청](https://www.python-httpx.org/async/),
[timeout](https://www.python-httpx.org/advanced/timeouts/),
[문자 인코딩](https://www.python-httpx.org/advanced/text-encodings/)을 따른다.

```python
import httpx
from llm.providers.web import WebFetch
from llm.components.tools.builtin import BuiltinTools

# 아래 숫자·옵션은 이 예제를 실행하는 Application의 명시적 선택이다.
async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
    fetch = WebFetch(client, revision="company-web-v1")
    async with BuiltinTools(workspace, adapters={"web_fetch": fetch}) as toolkit:
        tool, arguments = toolkit.registry.prepare("web_fetch", {
            "url": "https://example.com/", "max_bytes": 1_000_000, "max_chars": 8_000,
        })
        # 호스트 직접 호출 예제. Agent 실행은 기존 ToolExecutor를 사용한다.
        page = await tool.handler(arguments)
```

응답에는 요청 URL·최종 URL·title·본문·링크·content_type·본문 SHA-256·truncated가 있다.
HTML의 script/style/template은 실행하거나 본문에 넣지 않고 상대 링크는 최종 URL 기준으로
해석한다. 링크를 자동 방문하지 않는다. charset은 SDK 응답 정보를 사용하며, 잘못된 인코딩과
미지원 형식은 명확한 오류다. truncation은 숨기지 않는다. 검색 결과·title·출처는 사실 검증이나
안전한 지침이라는 증명이 아니다.

ProviderCalls가 연결돼 있으면 기존 admission을 사용한다. 취소 시 요청/응답을 회수한 뒤
슬롯을 반환한다. 공유 client는 닫지 않는다. HTTP 오류를 자동 재시도하지 않으며 상세 서버
본문을 기본 오류 메시지에 넣지 않는다. 원격 GET을 취소해도 서버 처리 종료를 보증하지 않는다.

## 검색 서비스 연결

`WebSearch(search, revision=..., options=...)`의 `search(arguments, options)`는
Application이 주입한 async 서비스 어댑터다. query, 명시된 limit/cursor만 전달하며
options는 해석하지 않는 사본이다. 선택한 서비스가 옵션 검증과 SDK 응답 변환을 소유한다.
결과는 `SearchPage(results=[WebSource(url, title, snippet), ...], next_cursor=...)` 또는
동일한 closed JSON이다. 다음 페이지는 Application/Agent가 명시적으로 요청한다.
서비스가 제공하지 않은 출처·인용·페이지를 Backend가 만들어내지 않는다.

`BuiltinTools(..., adapters={"web_search": search_adapter, "web_fetch": fetch_adapter})`와
기존 Component `config.enabled`로 필요한 Tool을 선택한다. 서비스 선택, Loop 지침,
Tool 노출, 자동 검색이나 허용 정책을 이 모듈에서 변경하지 않는다. Host 어댑터의 효과는
기존 보수적인 contract를 유지한다. 이 기능 추가만으로 Hub가 인터넷 검색을 제공하게 되지는 않는다.

어댑터 revision은 Host 실행 설정의 identity다. 서비스·인증·네트워크 정책이 바뀌면 Host가
revision도 변경해야 한다. 래퍼는 execution_binding과 선택/이벤트 hook을 보존하며,
SDK client 객체나 비밀정보를 binding에 직렬화하지 않는다.
