# pr-review-squad

PR을 **쓰고(작성)**, **읽는(리뷰)** 일을 여러 AI 역할로 나눠 맡기는 스킬 모음입니다. 두 스킬 모두 "만드는 쪽과 확인하는 쪽을 분리"하고, 통과 여부를 AI의 인상이 아니라 **규칙과 스크립트**로 정합니다. Claude Code와 Codex에서 쓸 수 있는 `SKILL.md` 형식입니다.

| 스킬 | 하는 일 | 요청 예 |
|---|---|---|
| [`pr-write-squad`](skills/pr-write-squad) | 브랜치의 변경으로 PR 제목과 본문을 쓰고, 본문의 주장을 diff와 실제 실행 결과로 사실 확인한 뒤, 요청하면 push하고 PR을 생성 | "PR 만들어줘", "PR 설명 써줘" |
| [`pr-review-squad`](skills/pr-review-squad) | PR을 관점별 리뷰어 4명이 동시에 검토하고, 별도의 검증자가 지적을 반박해 통과한 것만 GitHub 마크다운 보고서로 보여 주며, 요청하면 PR에 인라인·요약 댓글로 남김 | "이 PR 리뷰해줘", "리뷰하고 댓글로 남겨줘" |

두 스킬은 이어서 쓸 수 있습니다. **쓰고 → (같은 변경을) 리뷰로 점검하고 → 올린다**는 흐름입니다.

## 설계: 하네스 6요소

두 스킬 모두 같은 6요소로 짰습니다. 상세 대응은 각 스킬의 README에 있습니다.

| 요소 | 작성 스킬 | 리뷰 스킬 |
|---|---|---|
| Role | 작성자 / 사실 확인자 / 처음 읽는 리뷰어 | 관점별 리뷰어 4명 / 검증자 / 종합자 |
| Rubric | 여섯 가지 기준 + `validate_pr.py` | 3단계 등급 + 규칙 기반 판정 + `validate_findings.py` |
| Context | PR 템플릿, 최근 PR, 커밋, diff | 팀 규칙, PR 본문, diff |
| Tools | `collect_context.py`, `validate_pr.py`, `create_pr.py` | `collect_diff.sh`, `validate_findings.py`, `render_report.py`, `post_review.py` |
| Workflow | 수집 → 초안 → 사실 확인 → 읽기 검토 → 검사 → 생성 | 수집 → 동시 리뷰 → 반박 검증 → 종합 → 검사 → 보고 → 게시 |
| Guardrail | 안 돌린 테스트를 통과로 쓰지 않기, 강제 push·커밋 금지 | 코드 수정·승인/변경 요청 금지, 비밀 값 검사 |

## 설치

저장소를 받아서 스킬 폴더를 스킬 위치에 복사합니다. 두 스킬이 각각 독립된 폴더입니다.

```bash
git clone https://github.com/SYHER/pr-review-squad.git
cd pr-review-squad
```

```bash
# Claude Code: 두 스킬 모두 설치
mkdir -p ~/.claude/skills && cp -r skills/* ~/.claude/skills/

# Codex
mkdir -p ~/.agents/skills && cp -r skills/* ~/.agents/skills/
```

저장소를 업데이트할 때마다 다시 복사하기 싫다면 복사 대신 링크를 걸어도 됩니다.

```bash
ln -s "$PWD/skills/pr-write-squad"  ~/.claude/skills/pr-write-squad
ln -s "$PWD/skills/pr-review-squad" ~/.claude/skills/pr-review-squad
```

한 스킬만 쓰려면 해당 폴더만 복사하세요. 새 세션부터 적용됩니다. GitHub 연동에는 [`gh` CLI](https://cli.github.com/) 로그인이 필요하고, Python 3.9 이상(3.9와 3.12에서 시험)과 `git`이 있어야 합니다(외부 패키지 없음).

## 사용 예

```
PR 만들어줘                      → pr-write-squad: 제목·본문 작성 → 사실 확인 → push → PR 생성
이 브랜치 PR 설명만 써줘          → pr-write-squad: 제목·본문만 보여 줌
이 PR 리뷰해줘                   → pr-review-squad: 채팅에 보고서
이 PR 리뷰하고 댓글로 남겨줘       → pr-review-squad: 인라인·요약 댓글 게시
```

PR 템플릿은 `python3 skills/pr-write-squad/scripts/configure.py --template <경로>`로 한 번 지정해 두면 어떤 저장소에서도 그 형식으로 씁니다(저장소 자체의 템플릿보다 우선, 설정은 저장소 밖 `~/.config`에 저장).

두 스킬 모두 **요청이 곧 확인**입니다. PR을 만들거나 댓글을 올리라고 요청했다면 다시 묻지 않고 하고, 요청하지 않았다면 하지 않습니다. 잘못 올린 리뷰 댓글은 `post_review.py --retract`로 한 번에 철회할 수 있습니다.

## 테스트

```bash
python3 -m unittest discover -s skills/pr-review-squad/tests
python3 -m unittest discover -s skills/pr-write-squad/tests
```

두 스킬의 테스트는 실제 GitHub에 아무것도 보내지 않습니다(`gh`는 호출을 기록하는 가짜로 대체).

## 한계

- 검증자와 사실 확인자도 AI라서, 리뷰어나 작성자와 같은 오해를 공유할 수 있습니다. 스크립트는 근거의 **형식과 위치**를 확인할 뿐 내용의 정확성은 모릅니다. 사람의 리뷰를 대체하지 않습니다.
- 시험 횟수가 많지 않아서(실제 PR 한 건과 시험용 PR) 효과를 통계적으로 말할 수는 없습니다.

## 라이선스

MIT. [LICENSE](LICENSE) 참고.
