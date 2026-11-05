# 기본 PR 본문 양식

저장소에 PR 템플릿이 없을 때만 쓴다. 템플릿이 있으면 그 제목들이 우선이다. `(선택)`이 붙은 섹션은 비워도 된다.

## 한국어

```markdown
## 개요
이 PR이 동작을 어떻게 바꾸는지 한두 문장.

## 변경 이유
왜 필요한지. 이슈 번호, 버그, 요구.

## 주요 변경
- 동작 단위로 나열 (파일 이름 나열이 아니라 무엇이 달라지는지)

## 이번에 하지 않은 것
- 일부러 뺀 것, 후속 작업. 없으면 "없음"

## 확인 방법
- 실행한 명령과 결과. 실행하지 못한 것은 이유와 함께

## 위험과 되돌리는 방법
- 영향받는 동작, 되돌리는 방법

## 먼저 봐 주세요 (선택)
- 핵심 파일이나 함수
```

## English

```markdown
## Summary
How this PR changes behavior, in one or two sentences.

## Why
Why it is needed. Issue number, bug, requirement.

## Changes
- List by behavior, not by file name

## Not in this PR
- What is deliberately left out, follow-ups. "None" if nothing

## Verification
- Commands you ran and their results. State what you could not run and why

## Risk and rollback
- What behavior is affected, how to revert

## Where to look first (optional)
- Key files or functions
```

## 제목

- 저장소 관례가 있으면 따른다(예: `feat(scope): 요약`, `PROJ-123 요약`).
- 브랜치 이름이 `PROJ-123/...`처럼 이슈 키로 **맨 앞**에서 시작하면 제목 맨 앞에 `PROJ-123 `이 자동으로 붙는다(스크립트가 처리하므로 제목에는 쓰지 않는다). 키가 맨 앞에 없으면 붙이지 않는다.
- 관례가 없으면 동사로 끝나는 한 문장, 72자 이내, 마침표 없이.
- 한 PR은 한 가지 목적만 가진다. 서로 다른 변경이 섞였다면 제목을 억지로 합치지 말고 사용자에게 분리를 제안한다.
