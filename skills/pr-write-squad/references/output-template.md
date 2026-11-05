# 파일 형식

작업 파일은 `$(git rev-parse --git-dir)/pr-write-squad/` 아래에 둔다.

## `title.txt`

한 줄. 저장소의 제목 형식(최근 PR, CONTRIBUTING)이 있으면 그대로 따른다.

## `body.md`

저장소 PR 템플릿이 있으면 그 제목들을 그대로 쓰고 채운다. 없으면 [conventions-template.md](conventions-template.md)의 기본 양식을 쓴다. 안내 주석(`<!-- ... -->`)은 모두 지운다.

## `claims.json` (사실 확인 대상)

본문에서 검증 가능한 문장마다 한 건. 사실 확인자가 `verdict`, `evidence`, `note`를 채운다.

```json
[
  {
    "id": "K1",
    "kind": "behavior",
    "text": "discount=1.0이면 합계가 0원이 된다",
    "evidence": "src/payment.py:12",
    "verdict": "SUPPORTED",
    "note": ""
  }
]
```

| 필드 | 필수 | 규칙 |
|---|---|---|
| `id` | O | 중복 없는 문자열 |
| `kind` | O | `change`, `behavior`, `test`, `scope` 중 하나 |
| `text` | O | 본문에 실제로 쓴 주장 |
| `evidence` | `SUPPORTED`는 O | `파일:줄` 또는 `파일:시작-끝` (줄 번호 필수). 여러 곳은 쉼표로 구분하고 항목 뒤에 괄호 설명을 붙일 수 있다. 예: `src/a.py:4 (정의), tests/test_a.py:15-21` |
| `verdict` | O | `SUPPORTED`, `UNSUPPORTED`, `UNCERTAIN`. 검증 전에는 빈 문자열 |
| `note` | `UNSUPPORTED`/`UNCERTAIN`은 O | 무엇이 다른지, 무엇이 더 있으면 판단 가능한지 |

## `verification.json` (실제로 실행한 것)

```json
[
  {
    "what": "단위 테스트",
    "command": "python3 -m unittest discover -s tests",
    "result": "passed",
    "evidence": "Ran 12 tests ... OK",
    "reason": ""
  },
  {
    "what": "린터",
    "command": "ruff check .",
    "result": "not_run",
    "evidence": "",
    "reason": "ruff가 설치되어 있지 않음"
  }
]
```

| `result` | 필수 필드 |
|---|---|
| `passed` | `command`, `evidence`(출력 일부) |
| `failed` | `command`, `evidence`. 본문에서 실패를 숨기지 않는다 |
| `not_run` | `reason`. 본문이 실행하지 못했다는 사실을 말해야 한다 |

본문에 "테스트 통과" 같은 표현을 쓰려면 `passed` 기록이 하나 이상 있어야 한다.

## 생성할 때 붙는 표식

`create_pr.py`는 본문 끝에 보이지 않는 표식(`<!-- pr-write-squad -->`)을 붙인다. 같은 브랜치에서 다시 실행하면 이 표식이 있는 본문만 갱신하고, 사람이 쓴 본문은 덮어쓰지 않는다.
