# pr-review-squad

PR이나 코드 변경을 **관점별 리뷰어 4명이 동시에 검토**하고, **별도의 검증자가 각 지적을 반박**해 본 뒤, 통과한 지적만 "꼭 고쳐야 할 문제 / 고치는 것을 권장 / 참고"로 나눠 GitHub 마크다운 보고서로 보여 주는 AI 코드 리뷰 스킬입니다. 코드를 수정하지 않고 승인이나 변경 요청도 제출하지 않습니다. 사용자가 "댓글로 남겨줘"라고 요청하면 PR에 인라인 댓글과 요약 댓글로 바로 남기고, 요청하지 않으면 올리지 않습니다. 올린 댓글은 한 번에 철회할 수 있습니다.

Claude Code와 Codex에서 쓸 수 있는 `SKILL.md` 형식입니다.

## 왜 이렇게 만들었나

AI 하나에게 "리뷰해줘"라고 하면 두 가지가 흔히 생깁니다.

- 지적한 사람이 자기 지적을 스스로 확인해서 근거 약한 지적이 그대로 남습니다 (자기 평가의 함정).
- 한 번에 여러 관점을 보려다 보안이나 테스트 같은 관점이 얕아집니다.

그래서 관점을 나눠 동시에 보고(Fan-out), 지적마다 작성자와 다른 검증자가 반박을 시도하게(Adversarial verification) 했습니다. 판정은 AI의 인상이 아니라 규칙과 스크립트로 정합니다.

## 하네스 6요소 대응

| 요소 | 이 스킬에서 |
|---|---|
| Role | 리뷰어 4명(정확성, 보안, 가독성·유지보수, 테스트), 검증자, 종합자 |
| Rubric | 3단계 등급(꼭 고쳐야 함 / 권장 / 참고, 내부 코드 C0/C1/C2), 규칙 기반 판정, 관점별 10점 요약, `validate_findings.py` |
| Context | 팀 규칙 문서와 PR 설명을 먼저 읽고, 없으면 기본 기준을 사용 |
| Tools | `collect_diff.sh`, `validate_findings.py`, 프로젝트의 린터와 테스트 러너 |
| Workflow | 범위 확정, 맥락 수집, 동시 리뷰, 적대적 검증, 종합, 스크립트 검증, 보고 |
| Guardrail | 코드 수정과 승인/변경 요청 금지, 댓글은 사용자가 요청했을 때만(열린 PR, 비밀 값 검사 자동 적용, 철회 가능), 근거 없는 지적 금지, 검증자 분리, 비밀 값 가리기 |

## 설치와 사용법

이 저장소 자체가 스킬 폴더입니다. 스킬 폴더 위치에 그대로 clone하면 됩니다.

```bash
# Claude Code
git clone https://github.com/SYHER/pr-review-squad.git ~/.claude/skills/pr-review-squad

# Codex
git clone https://github.com/SYHER/pr-review-squad.git ~/.agents/skills/pr-review-squad
```

새 세션에서 이렇게 요청합니다.

```
이 PR 리뷰해줘
main 기준으로 현재 브랜치 변경사항 검토해줘
이 diff 머지해도 될까?
이 PR 리뷰하고 댓글로 남겨줘
```

- 앞의 세 가지는 채팅에 보고서만 보여 줍니다. 마지막처럼 **게시를 요청해야** PR에 댓글이 올라갑니다.
- GitHub PR을 읽거나 댓글을 올리려면 [`gh` CLI](https://cli.github.com/)에 로그인되어 있어야 합니다.
- 어떤 보고서가 나오는지는 [`examples/sample-review.md`](examples/sample-review.md)에서 볼 수 있습니다.

## 구성 파일

```
pr-review-squad/
├── SKILL.md                      # 역할, 평가 기준, 진행 규칙, 가드레일
├── README.md
├── LICENSE
├── references/
│   ├── reviewer-roles.md         # 관점별 리뷰어와 검증자 지시 (의도 대조 절차 포함)
│   ├── rubric.md                 # 등급 정의, 판정 규칙, 점수표
│   ├── output-template.md        # findings JSON 필드와 보고서 양식
│   ├── subagent-prompts.md       # 리뷰어, 검증자 서브에이전트 프롬프트 템플릿
│   └── conventions-template.md   # 팀 규칙이 없을 때 쓰는 기본 기준
├── scripts/
│   ├── collect_diff.sh           # 로컬 범위 또는 GitHub PR의 diff 수집 (읽기 전용)
│   ├── validate_findings.py      # findings 형식 검사, 최종 판정 계산, 파일·줄 번호 대조(--root)
│   ├── render_report.py          # findings → GitHub 마크다운 보고서 (등급 코드 대신 쉬운 이름 사용)
│   └── post_review.py            # 보고서를 PR에 인라인·요약 댓글로 게시 (기본은 미리보기)
├── tests/
│   ├── test_validate_findings.py # 판정, 점수, 형식 검사 회귀 테스트
│   ├── test_render_report.py     # 보고서 형식 테스트
│   └── test_post_review.py       # 게시 안전장치 테스트 (가짜 gh 사용, 실제 게시 없음)
└── examples/
    ├── sample-findings.json      # 형식 예시이자 스크립트 동작 확인용
    └── sample-review.md          # sample-findings.json으로 만든 보고서 (실제 출력 모습)
```

## 의존성

- `git`
- Python 3.8 이상 (표준 라이브러리만 사용)
- 서브에이전트를 지원하는 환경이면 리뷰어와 검증자를 실제로 분리해서 실행합니다. 지원하지 않으면 순서대로 수행하고, 검증자를 분리하지 못했다는 점을 보고서에 적습니다.

## 스크립트 직접 실행

```bash
# base 브랜치 대비 변경 수집
bash scripts/collect_diff.sh origin/main

# GitHub PR 수집 (gh CLI 필요, 읽기 호출만)
bash scripts/collect_diff.sh --pr 123 -R owner/repo

# findings 검사와 판정
python3 scripts/validate_findings.py examples/sample-findings.json

# 지적한 파일, 줄 번호, 인용 코드를 체크아웃된 저장소와 대조 (경고만 내며 판정은 바꾸지 않음)
python3 scripts/validate_findings.py findings.json --root /path/to/repo

# GitHub 마크다운 보고서 생성 (PR 댓글에 그대로 붙일 수 있는 형식)
python3 scripts/render_report.py findings.json \
  --not-reviewed "테스트와 린터는 실행하지 못했습니다" > review.md

# PR에 댓글로 게시: --post 없이 실행하면 미리보기만, 붙이면 실제로 올림
# (스킬을 쓸 때 "댓글로 남겨줘"라고 요청하면 에이전트가 바로 --post까지 실행합니다)
python3 scripts/post_review.py findings.json --pr 123 -R owner/repo \
  --not-reviewed "테스트와 린터는 실행하지 못했습니다"
python3 scripts/post_review.py findings.json --pr 123 -R owner/repo \
  --not-reviewed "테스트와 린터는 실행하지 못했습니다" --post

# 올린 댓글 철회: 이 스킬이 올린 내 댓글만 지움 (--yes 없이는 대상만 보여 줌)
python3 scripts/post_review.py --pr 123 -R owner/repo --retract
python3 scripts/post_review.py --pr 123 -R owner/repo --retract --yes

# 회귀 테스트
python3 -m unittest discover -s tests
```

## 시험해 본 결과

실제 업무용 저장소의 열린 PR(파일 11개, diff 약 1,100줄)에 여러 번 돌려 보고, 일부러 문제를 심은 작은 PR(비공개 시험 저장소)에서 댓글 게시까지 확인했습니다.

**잘 된 것**
- 심어 둔 문제(0으로 나누기, SQL 문자열 조립, 구현을 베낀 테스트 기대값)를 모두 찾았고, 검증자가 모두 코드로 확인했습니다.
- 같은 PR을 여러 번 돌려도 판정과 가장 큰 권고(새 화면 동작의 테스트 부재)는 매번 같았습니다. 테스트 부재를 어떤 등급으로 올릴지 조건을 `rubric.md`에 명시한 뒤에는 같은 지적의 등급이 흔들리지 않았습니다.
- 인라인 댓글과 요약 댓글이 지정한 줄에 올라가고, 승인이나 변경 요청은 생기지 않았습니다. 재실행해도 중복되지 않고, `--retract`로 한 번에 지울 수 있음을 실제 GitHub에서 확인했습니다.

**시험하다 찾아서 고친 것**
- 같은 모델이 리뷰어와 검증자를 맡아 **오독을 공유**한 적이 있습니다. 검증자 둘이 "다른 코드가 덮어써서 테스트가 닿지 않는다"는 과장된 지적을 그대로 확인했습니다. 그래서 테스트 부재 지적은 검증자가 호출 경로를 줄 번호로 추적해 `trace`로 보여야 확인할 수 있게 했습니다. 새 규칙의 검증자는 같은 지적을 올바르게 반려했습니다.
- 리뷰어가 줄 번호를 틀리게 적은 적이 있습니다(53줄짜리 파일에서 396줄을 가리킴, 다른 경우는 12줄 오차). 지적한 파일, 줄, 인용 코드를 저장소와 대조하는 `validate_findings.py --root`를 추가했습니다.
- 등급별 집계 오류, 안내문 위치, 제목에 처리 과정 메모가 노출되는 문제를 고치고 회귀 테스트를 추가했습니다.

시험 횟수가 적어서 위 규칙들의 효과를 통계적으로 말할 수는 없습니다.

## 한계

- 코드를 실행해서 확인하는 범위는 프로젝트에 이미 있는 테스트와 린터까지입니다. 설치나 네트워크 호출은 하지 않습니다.
- 검증자도 AI라서 같은 오해를 공유할 수 있습니다. 판정이 `APPROVE`여도 사람의 리뷰를 대체하지 않습니다.
- diff가 1500줄을 넘으면 경고합니다. 큰 PR은 파일 단위로 나눠 리뷰하는 것이 좋습니다.

## 라이선스

MIT. [LICENSE](LICENSE) 참고.
