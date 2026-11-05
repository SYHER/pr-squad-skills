# pr-write-squad

현재 브랜치의 변경으로 **PR 제목과 본문을 쓰고**, **쓴 사람과 다른 사실 확인자가 본문의 주장을 하나씩 diff와 대조**하고, **처음 읽는 사람의 눈으로 이해되는지 검토**한 뒤, 요청하면 push하고 PR을 만드는 AI 스킬입니다. 저장소의 PR 템플릿과 최근 PR의 말투를 따릅니다.

Claude Code와 Codex에서 쓸 수 있는 `SKILL.md` 형식이고, 같은 저장소의 [`pr-review-squad`](../pr-review-squad)와 짝입니다(작성한 PR을 리뷰 스킬로 리뷰할 수 있습니다).

## 왜 이렇게 만들었나

AI에게 "PR 설명 써줘"라고 하면 글은 그럴듯하게 나오지만 두 가지가 자주 틀립니다.

- **돌려 보지 않은 테스트를 "통과했습니다"라고 씁니다.**
- **diff에 없는 변경이나 동작을 설명합니다.**

그래서 본문의 주장을 따로 뽑아(`claims.json`) 쓴 사람이 아닌 다른 에이전트가 diff와 대조하게 하고(Adversarial verification), "통과"라는 표현은 실제 실행 기록(`verification.json`)이 있을 때만 허용하도록 스크립트가 막습니다. 통과 여부는 AI의 인상이 아니라 규칙과 스크립트로 정합니다.

## 하네스 6요소 대응

| 요소 | 이 스킬에서 |
|---|---|
| Role | 작성자, 사실 확인자(본문은 못 보고 주장과 diff만 봄), 처음 읽는 리뷰어(코드는 못 보고 글만 봄), 종합자 |
| Rubric | 여섯 가지 기준(무엇/왜/확인/범위/위험/먼저 볼 곳), 통과 조건 3가지, `validate_pr.py`의 결정적 검사 |
| Context | 저장소의 PR 템플릿, CONTRIBUTING, 최근 머지된 PR 제목, 커밋, 이슈 번호 단서, diff |
| Tools | `collect_context.py`, `validate_pr.py`, `create_pr.py`, 프로젝트의 테스트와 린터 |
| Workflow | 범위 확정 → 맥락 수집 → 검증 실행 → 초안 → 사실 확인 → 읽기 검토 → 검사 → (요청 시) 생성 |
| Guardrail | 실행 안 한 테스트를 통과로 쓰지 않기, 커밋·강제 push·main에서 PR 금지, 요청 없으면 생성 안 함, 사람이 쓴 본문 덮어쓰기 금지, 비밀 값 거부 |

## 사용법

이 스킬 폴더를 스킬 위치에 복사합니다(저장소 루트의 [README](../../README.md) 참고). 새 세션에서 이렇게 요청합니다.

```
PR 만들어줘
이 브랜치 PR 설명 써줘
PR 본문 작성해줘
```

- "PR 설명 써줘"처럼 생성을 말하지 않으면 제목과 본문만 보여 주고 "PR로 만들까요?"라고 제안합니다.
- "PR 만들어줘", "올려줘"처럼 생성을 요청하면 그 요청이 확인이라서, 다시 묻지 않고 push하고 PR을 만든 뒤 링크와 draft 여부를 알려 줍니다. draft로 만들고 싶으면 "draft로"라고 말하세요.
- 커밋은 하지 않습니다. 변경은 미리 커밋해 두어야 합니다.

## 커밋을 더한 뒤에는 (PR 설명 갱신)

PR을 만든 뒤 커밋을 push해도 **PR 설명은 저절로 바뀌지 않습니다.** GitHub는 커밋과 diff만 갱신하고 제목과 본문은 그대로 둡니다. 그래서 설명에 "테스트 3개 추가", "새 파일 2개" 같은 낡은 문장이 남을 수 있습니다.

push한 뒤 "PR 내용 갱신해줘"라고 요청하면 스킬이 현재 상태로 다시 모으고, 이전 본문을 보지 않고 새로 쓰고, 사실 확인과 검사를 다시 거친 뒤, 같은 브랜치의 열린 PR을 새로 만들지 않고 갱신합니다. 이 스킬이 만든 본문(보이지 않는 표식이 있는 것)만 갱신하고, 사람이 직접 고친 본문은 `--overwrite` 없이는 건드리지 않습니다. 자동 갱신 기능은 없습니다.

## 브랜치 이름의 이슈 키를 제목 맨 앞에

브랜치 이름이 `PROJ-190/feat/add-discount`처럼 **맨 앞에 이슈 키**(대문자 프로젝트 코드-숫자)로 시작하면, PR 제목 맨 앞에 `PROJ-190 `이 자동으로 붙습니다(예: `PROJ-190 할인율 적용을 추가한다`).

- 키가 브랜치 이름 맨 앞에 없으면(`feat/PROJ-190-x`, `fix-12/x`, `proj-190/x` 등) 아무것도 붙이지 않습니다. `fix-12` 같은 흔한 이름이 키로 오인되지 않도록 대문자 키만 인정합니다.
- 스크립트(`validate_pr.py`, `create_pr.py`)가 붙이므로 작성자 AI가 빠뜨리거나 두 번 쓰는 일이 없습니다. 제목이 이미 그 키로 시작하면 다시 붙이지 않고, 갱신할 때도 같은 규칙입니다.
- 제목 길이 한도(72자)와 `--title-pattern`은 키가 붙은 최종 제목 기준입니다.
- 끄려면 `--no-issue-key`를 쓰세요.

## 항상 같은 템플릿으로 쓰기

저장소마다 PR 템플릿이 다른데 늘 같은 형식으로 쓰고 싶다면 템플릿을 한 번 지정해 둡니다.

```bash
python3 scripts/configure.py --template /경로/pull_request_template.md   # 지정
python3 scripts/configure.py --show                                       # 확인
python3 scripts/configure.py --clear                                      # 해제
```

- 설정은 저장소 밖의 `~/.config/pr-write-squad/config.json`에 저장되어(`PR_WRITE_CONFIG`로 위치 변경 가능), 내 컴퓨터의 경로가 공개 저장소에 들어가지 않습니다.
- 지정한 템플릿은 **저장소 자체의 PR 템플릿보다 우선**합니다. 저장소 템플릿을 쓰고 싶으면 `--clear`로 해제하세요. 설정한 파일이 사라지면 다른 템플릿으로 슬쩍 대신하지 않고 멈춥니다.
- 템플릿의 섹션 제목, 체크박스 선택지, `What:`/`Why:` 같은 항목 라벨이 본문에 그대로 있는지 검사합니다. 비어 있는 목록 항목과 안내 주석이 남아 있으면 막습니다.
- "테스트를 했습니다" 같은 체크박스는 실제로 통과한 실행 기록이 있을 때만 체크할 수 있습니다. 확인할 수 없는 항목(예: 커밋 컨벤션 준수)을 체크하면 경고합니다.

## 구성 파일

```
pr-write-squad/
├── SKILL.md                      # 역할, 평가 기준, 진행 규칙, 가드레일
├── README.md
├── references/
│   ├── author-roles.md           # 작성자, 사실 확인자, 처음 읽는 리뷰어 지시
│   ├── rubric.md                 # 여섯 가지 기준, 통과 조건, 검사 코드
│   ├── output-template.md        # title/body/claims/verification 파일 형식
│   ├── subagent-prompts.md       # 서브에이전트 프롬프트 템플릿
│   └── conventions-template.md   # 템플릿이 없을 때 쓰는 기본 PR 양식(한국어, 영어)
├── scripts/
│   ├── collect_context.py        # 브랜치, 커밋, 템플릿, 최근 PR 제목, diff 수집 (읽기 전용)
│   ├── validate_pr.py            # 제목·본문·주장·검증 기록 결정적 검사
│   ├── create_pr.py              # push와 PR 생성/갱신 (기본은 미리보기)
│   ├── configure.py              # 항상 쓸 PR 템플릿 지정, 확인, 해제
│   └── _common.py                # 공용 도구(실행되는 git 찾기, 비밀 값 패턴)
├── tests/                        # 단위 테스트 (실제 임시 git 저장소와 가짜 gh 사용)
└── examples/                     # validate_pr.py를 통과하는 예시 제목, 본문, 주장, 검증 기록
```

## 의존성

- `git`, Python 3.9 이상(표준 라이브러리만 사용, 3.9와 3.12에서 시험)
- GitHub 연동에는 [`gh` CLI](https://cli.github.com/) 로그인이 필요합니다.
- 서브에이전트를 지원하는 환경이면 작성자, 사실 확인자, 읽기 검토를 실제로 분리해 실행합니다. 지원하지 않으면 순서대로 수행하고 분리하지 못했다는 점을 알립니다.
- PATH 앞쪽에 실행되지 않는 `git`(다른 아키텍처용 바이너리 등)이 있어도 실행되는 `git`을 찾아 쓰고, `gh`를 부를 때도 그 `git`이 있는 폴더를 PATH 맨 앞에 둡니다(`gh`는 내부에서 `git`을 부릅니다).
- 네트워크를 쓰는 호출에는 제한 시간(기본 120초, 환경 변수 `PR_WRITE_TIMEOUT`)이 있습니다. git 자격 증명 도우미(예: macOS 키체인)가 응답하지 않아 멈추는 환경이 있어서, 무한정 기다리지 않고 이유와 함께 중단합니다. 원격 조회가 실패하면 "원격에 브랜치가 없다"고 착각해 push하지 않고 멈춥니다.

## 스크립트 직접 실행

```bash
# 맥락 수집 (읽기 전용)
python3 scripts/collect_context.py --base main

# 검사
python3 scripts/validate_pr.py --title-file title.txt --body-file body.md \
  --claims claims.json --verification verification.json --template .github/pull_request_template.md

# 미리보기(기본), 실제 생성은 --post
python3 scripts/create_pr.py --title-file title.txt --body-file body.md \
  --claims claims.json --verification verification.json
python3 scripts/create_pr.py --title-file title.txt --body-file body.md \
  --claims claims.json --verification verification.json --post

# 회귀 테스트
python3 -m unittest discover -s tests
```

## 한계

- 사실 확인자도 AI라서 같은 오해를 공유할 수 있습니다. 스크립트는 근거가 **형식에 맞게 있는지**(`파일:줄`)만 확인하고 내용이 맞는지는 모릅니다.
- "이유"는 코드에서 알 수 없을 때가 많습니다. 커밋과 이슈에 이유가 없으면 지어내지 않고 사용자에게 물어봅니다.
- 변경이 여러 목적을 섞고 있으면 좋은 설명이 나오기 어렵습니다. 그때는 PR을 나누라고 제안합니다.
- 프로젝트의 테스트 명령을 찾지 못하면 "실행하지 못함"으로 기록하며, 그 사실이 본문에 들어갑니다.
