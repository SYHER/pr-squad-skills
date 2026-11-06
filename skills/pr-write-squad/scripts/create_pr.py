#!/usr/bin/env python3
"""검증을 통과한 제목과 본문으로 PR을 만든다. 기본은 미리보기이고 --post를 줘야 push하고 PR을 만든다.

사용법:
  python3 create_pr.py --title-file title.txt --body-file body.md --claims claims.json
        --verification verification.json [--template 템플릿.md] [--title-pattern 정규식]
        [--base main] [--head 브랜치] [-R owner/repo] [--draft] [--overwrite] [--post]

하는 일(--post일 때만): 브랜치가 원격에 없거나 앞서 있으면 push(강제 push 없음) → 같은 브랜치의
열린 PR이 없으면 생성, 있으면 이 스킬이 만든 본문일 때만 제목과 본문을 갱신 → GitHub에서 상태를 읽어 확인.

거부하는 경우: 검증 오류(validate_pr.py와 같은 검사), 사실 확인(--claims)과 검증 기록(--verification)이 없음
(--allow-unchecked로만 건너뜀), main/master에서 PR 만들기, 베이스보다 앞선 커밋이 없음,
사람이 쓴 본문이 있는 PR 덮어쓰기(--overwrite로만 허용).
종료 코드: 0 = 성공(미리보기 포함), 1 = 검사나 안전장치로 거부, 2 = 입력/git/gh 오류
환경 변수: PR_WRITE_GIT, PR_WRITE_GH (실행 파일 경로, 테스트용)
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import validate_pr as V  # noqa: E402

PROTECTED = ("main", "master")


class Stop(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def read_text(path, what):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise Stop(f"{what} 파일을 읽을 수 없음: {e}", 2)


def read_json(path, what):
    try:
        return json.loads(read_text(path, what))
    except json.JSONDecodeError as e:
        raise Stop(f"{what}가 올바른 JSON이 아님: {e}", 2)


def main(argv):
    ap = argparse.ArgumentParser(description="PR 생성(기본: 미리보기)")
    ap.add_argument("--title-file")
    ap.add_argument("--title")
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--claims")
    ap.add_argument("--verification")
    ap.add_argument("--template", help="지정하지 않으면 사용자 설정 > 저장소 템플릿 순으로 찾는다")
    ap.add_argument("--no-template", action="store_true", help="템플릿 구조 검사를 하지 않는다")
    ap.add_argument("--title-pattern")
    ap.add_argument("--no-issue-key", action="store_true", help="브랜치 이름의 이슈 키를 제목에 붙이지 않는다")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("-R", "--repo")
    ap.add_argument("--repo-dir", default=".")
    ap.add_argument("--draft", action="store_true", help="draft PR로 만든다(기본은 Ready for review)")
    ap.add_argument("--overwrite", action="store_true", help="사람이 쓴 기존 PR 본문도 덮어쓴다")
    ap.add_argument("--allow-unchecked", action="store_true", help="사실 확인과 검증 기록 없이도 허용")
    ap.add_argument("--post", action="store_true", help="실제로 push하고 PR을 만든다(없으면 미리보기)")
    args = ap.parse_args(argv[1:])
    C.use_utf8_stdio()
    cwd = str(Path(args.repo_dir).resolve())

    try:
        return run(args, cwd)
    except Stop as e:
        print(str(e), file=sys.stderr)
        return e.code
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2


def gh_json(args, cwd, what):
    """gh를 실행해 JSON으로 읽는다. 실패하거나 JSON이 아니면 Stop(2)."""
    r = C.gh(args, cwd)
    if not r.ok:
        raise Stop(f"{what}: {r.err or '알 수 없는 오류'}", 2)
    try:
        return json.loads(r.out or "null")
    except json.JSONDecodeError as e:
        raise Stop(f"{what}: gh 출력이 JSON이 아닙니다 ({e})", 2)


def remote_branch_sha(ls_out, head):
    """`git ls-remote` 출력에서 refs/heads/<head>와 정확히 같은 줄의 SHA만 고른다.

    ls-remote의 패턴은 끝부분 일치라서 'fix'를 조회하면 'user/fix'도 함께 나온다.
    첫 줄을 그대로 쓰면 다른 브랜치의 SHA와 비교하게 된다.
    """
    want = f"refs/heads/{head}"
    for line in ls_out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == want:
            return parts[0]
    return ""


def run(args, cwd):
    if args.title is None and not args.title_file:
        raise Stop("--title 또는 --title-file이 필요합니다", 2)
    title = (args.title if args.title is not None else read_text(args.title_file, "제목")).strip("\n")
    body = read_text(args.body_file, "본문")
    try:
        root = C.repo_root_of(cwd)
        tpath, tsource, _, _ = C.resolve_template(root, args.template, args.no_template)
    except C.ConfigError as e:
        raise Stop(str(e), 2)
    template = read_text(tpath, "템플릿") if tpath else None
    claims = read_json(args.claims, "claims") if args.claims else None
    verification = read_json(args.verification, "verification") if args.verification else None
    if (claims is None or verification is None) and not args.allow_unchecked:
        raise Stop("사실 확인(--claims)과 검증 기록(--verification)이 있어야 PR을 만듭니다. "
                   "건너뛰려면 --allow-unchecked를 쓰세요", 1)

    # 브랜치 이름 맨 앞의 이슈 키(예: PROJ-190)는 제목 맨 앞에 자동으로 붙는다. 붙인 최종 제목을 검사한다.
    branch_for_title = args.head or C.current_branch(cwd)
    title, issue_key = C.compose_title(title, branch_for_title, not args.no_issue_key)
    errors, warnings = V.validate(title, body, template, claims, verification, args.title_pattern)
    if errors:
        print("검사에서 오류가 나와 PR을 만들지 않습니다:", file=sys.stderr)
        for e in errors:
            print(f"  - [{e['code']}] {e['message']}", file=sys.stderr)
        return 1

    # --- git 상태 ---
    top = C.git(["rev-parse", "--show-toplevel"], cwd)
    if not top.ok:
        raise Stop("git 저장소가 아닙니다", 2)
    head = args.head or C.git(["rev-parse", "--abbrev-ref", "HEAD"], cwd).out
    if head in ("", "HEAD"):
        raise Stop("detached HEAD 상태입니다. 브랜치로 이동하세요", 2)
    if head in PROTECTED:
        raise Stop(f"{head} 브랜치에서는 PR을 만들지 않습니다. 기능 브랜치를 쓰세요", 1)

    # --- GitHub 정보 ---
    view_args = ["repo", "view"] + ([args.repo] if args.repo else []) + ["--json", "nameWithOwner,defaultBranchRef"]
    info = gh_json(view_args, cwd, "저장소 정보를 가져오지 못했습니다")
    repo = args.repo or info["nameWithOwner"]
    base = args.base or info["defaultBranchRef"]["name"]
    if head == base:
        raise Stop(f"head와 base가 같습니다({base})", 1)

    base_ref = f"origin/{base}" if C.git(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}"], cwd).ok else base
    if not C.git(["rev-parse", "--verify", "--quiet", base_ref], cwd).ok:
        raise Stop(f"베이스를 찾을 수 없습니다: {base}", 2)
    ahead = int(C.git(["rev-list", "--count", f"{base_ref}..{head}"], cwd).out or 0)
    if ahead == 0:
        raise Stop(f"{base_ref}보다 앞선 커밋이 없어 PR을 만들 수 없습니다", 1)
    dirty = [l for l in C.git(["status", "--porcelain"], cwd).out.splitlines() if l.strip()]

    local = C.git(["rev-parse", head], cwd).out
    ls = C.git(["ls-remote", "--heads", "origin", f"refs/heads/{head}"], cwd)
    if not ls.ok:
        # 조회에 실패했는데 "원격에 없음"으로 착각해 push하는 일이 없도록 여기서 멈춘다
        raise Stop(f"원격 저장소를 조회하지 못했습니다(아무것도 push하지 않음): {ls.err or '알 수 없는 오류'}", 2)
    remote_sha = remote_branch_sha(ls.out, head)
    needs_push = remote_sha != local
    if needs_push and remote_sha:
        if not C.git(["merge-base", "--is-ancestor", remote_sha, local], cwd).ok:
            raise Stop("원격 브랜치가 로컬과 갈라져 있습니다. 강제 push는 하지 않으니 직접 정리하세요", 1)

    existing = gh_json(["pr", "list", "-R", repo, "--head", head, "--state", "open",
                        "--json", "number,url,isDraft,body", "--limit", "5"], cwd,
                       "기존 PR을 조회하지 못했습니다") or []
    update = existing[0] if existing else None
    if update and V.MARKER not in (update.get("body") or "") and not args.overwrite:
        raise Stop(f"이미 열린 PR(#{update['number']})이 있고 본문이 이 스킬이 만든 것이 아닙니다. "
                   "사람이 쓴 본문을 덮어쓰지 않습니다(--overwrite로만 허용): " + update["url"], 1)

    final_body = body.rstrip("\n") + "\n\n" + V.MARKER + "\n"

    mode = "게시" if args.post else "미리보기(dry-run, push와 PR 생성을 하지 않음)"
    print(f"== {mode}: {repo}  {head} → {base}")
    print(f"- 제목: {title}" + (f"  (이슈 키 {issue_key}는 브랜치 이름 맨 앞에서 가져옴)" if issue_key else ""))
    print(f"- 템플릿: " + (f"{tpath} (출처: {tsource})" if tpath else "없음(기본 양식)"))
    print(f"- 본문: {len(final_body)}자, 검사 오류 0건" + (f", 경고 {len(warnings)}건" if warnings else ""))
    for w in warnings:
        print(f"    · 경고 [{w['code']}] {w['message']}")
    print(f"- 베이스보다 앞선 커밋: {ahead}개" + (f", 커밋되지 않은 변경 {len(dirty)}개 파일은 PR에 포함되지 않음" if dirty else ""))
    print(f"- push: " + ("필요(강제 push 없음)" if needs_push else "필요 없음(원격과 같음)"))
    if update:
        print(f"- 동작: 기존 PR #{update['number']}의 제목과 본문을 갱신 (draft 상태는 바꾸지 않음) {update['url']}")
    else:
        print(f"- 동작: 새 PR 생성 ({'draft' if args.draft else 'Ready for review'})")

    if not args.post:
        print("\n실제로 만들려면 같은 명령에 --post를 붙이세요.")
        return 0

    if needs_push:
        p = C.git(["push", "-u", "origin", head], cwd)
        if not p.ok:
            raise Stop(f"push 실패: {p.err}", 2)
        print(f"push 완료: origin/{head}")

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(final_body)
        body_path = fh.name
    try:
        url = _create_or_update(args, cwd, repo, base, head, title, body_path, update)
    finally:
        try:
            os.unlink(body_path)  # 본문 임시 파일을 남기지 않는다
        except OSError:
            pass

    v = C.gh(["pr", "view", url, "-R", repo, "--json", "state,isDraft,baseRefName,headRefName,url"], cwd)
    s = None
    if v.ok:
        try:
            s = json.loads(v.out)
        except json.JSONDecodeError:
            s = None
    if s:
        print(f"확인: state={s['state']} draft={s['isDraft']} {s['headRefName']} → {s['baseRefName']}")
        if not update and bool(s["isDraft"]) != bool(args.draft):
            print("경고: draft 상태가 요청과 다릅니다", file=sys.stderr)
    else:
        print(f"경고: 생성 후 상태를 읽지 못했습니다: {v.err or v.out}", file=sys.stderr)
    return 0


def _create_or_update(args, cwd, repo, base, head, title, body_path, update):
    if update:
        e = C.gh(["pr", "edit", str(update["number"]), "-R", repo, "--title", title, "--body-file", body_path], cwd)
        if not e.ok:
            raise Stop(f"PR 갱신 실패: {e.err}", 2)
        url = update["url"]
        print(f"PR 갱신됨: {url}")
        return url
    else:
        cmd = ["pr", "create", "-R", repo, "--base", base, "--head", head, "--title", title,
               "--body-file", body_path] + (["--draft"] if args.draft else [])
        c = C.gh(cmd, cwd)
        if not c.ok:
            raise Stop(f"PR 생성 실패: {c.err}", 2)
        m = re.search(r"https?://\S+", c.out)
        url = m.group(0) if m else c.out.strip()
        print(f"PR 생성됨: {url}")
    return url


if __name__ == "__main__":
    sys.exit(main(sys.argv))
