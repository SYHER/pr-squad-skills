#!/usr/bin/env python3
"""PR 작성에 필요한 맥락을 한 번에 모아 출력한다. 읽기 전용: 저장소와 GitHub를 수정하지 않는다.

사용법:
  python3 collect_context.py [--base 브랜치] [-R owner/repo] [--no-gh] [--repo-dir 경로]

출력(섹션): branch, commits, changed files, stat, pr template, contributing, recent merged PR titles,
            issue hints, diff
종료 코드: 0 = 성공, 1 = 만들 PR이 없음(베이스보다 앞선 커밋이 없음 등), 2 = 사용/환경 오류
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

CONTRIBUTING_FILES = ["CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md"]
CONTRIB_KEYWORDS = re.compile(r"(?i)(pull request|\bPR\b|풀 리퀘스트|커밋|commit|제목|title|branch|브랜치)")
ISSUE_PATTERNS = [r"#\d+", r"\b[A-Z][A-Z0-9]+-\d+\b"]


class Fail(Exception):
    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


def resolve_base(cwd, requested):
    if requested:
        return requested
    r = C.git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd)
    if r.ok and r.out.startswith("origin/"):
        return r.out[len("origin/"):]
    for cand in ("main", "master", "develop"):
        for ref in (f"refs/heads/{cand}", f"refs/remotes/origin/{cand}"):
            if C.git(["rev-parse", "--verify", "--quiet", ref], cwd).ok:
                return cand
    raise Fail("베이스 브랜치를 찾지 못했습니다. --base로 지정하세요")


def base_ref_for(cwd, base):
    if C.git(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}"], cwd).ok:
        return f"origin/{base}"
    if C.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{base}"], cwd).ok:
        return base
    raise Fail(f"베이스 브랜치를 찾을 수 없습니다: {base}")


def contributing_excerpt(root, limit=40):
    for rel in CONTRIBUTING_FILES:
        p = root / rel
        if p.is_file():
            lines = [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines()
                     if CONTRIB_KEYWORDS.search(l)]
            return rel, lines[:limit]
    return None, []


def issue_hints(*texts):
    found = []
    for t in texts:
        for pat in ISSUE_PATTERNS:
            for m in re.findall(pat, t):
                if m not in found:
                    found.append(m)
    return found


def main(argv):
    ap = argparse.ArgumentParser(description="PR 작성 맥락 수집(읽기 전용)")
    ap.add_argument("--base")
    ap.add_argument("-R", "--repo", help="owner/repo (최근 PR 제목 조회용)")
    ap.add_argument("--no-gh", action="store_true", help="gh를 호출하지 않는다")
    ap.add_argument("--repo-dir", default=".")
    ap.add_argument("--max-diff-lines", type=int, default=1500)
    args = ap.parse_args(argv[1:])
    C.use_utf8_stdio()
    cwd = str(Path(args.repo_dir).resolve())

    try:
        top = C.git(["rev-parse", "--show-toplevel"], cwd)
        if not top.ok:
            raise Fail("git 저장소가 아닙니다")
        root = Path(top.out)
        head = C.git(["rev-parse", "--abbrev-ref", "HEAD"], cwd).out
        if head == "HEAD":
            raise Fail("detached HEAD 상태입니다. 브랜치로 이동한 뒤 다시 실행하세요")
        base = resolve_base(cwd, args.base)
        base_ref = base_ref_for(cwd, base)
    except Fail as e:
        print(str(e), file=sys.stderr)
        return e.code
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

    if head == base:
        print(f"현재 브랜치가 베이스({base})와 같습니다. 기능 브랜치에서 실행하세요", file=sys.stderr)
        return 1
    ahead = int(C.git(["rev-list", "--count", f"{base_ref}..HEAD"], cwd).out or 0)
    if ahead == 0:
        print(f"{base_ref}보다 앞선 커밋이 없어 만들 PR이 없습니다", file=sys.stderr)
        return 1

    merge_base = C.git(["merge-base", base_ref, "HEAD"], cwd).out[:7]
    dirty = [l for l in C.git(["status", "--porcelain"], cwd).out.splitlines() if l.strip()]
    commits = C.git(["log", "--format=%h %s", f"{base_ref}..HEAD"], cwd).out
    commit_bodies = C.git(["log", "--format=%h%n%b%n--", f"{base_ref}..HEAD"], cwd).out
    names = C.git(["diff", "--name-status", f"{base_ref}...HEAD"], cwd).out
    stat = C.git(["diff", "--stat", f"{base_ref}...HEAD"], cwd).out
    diff = C.git(["diff", "-U3", f"{base_ref}...HEAD"], cwd).out

    print("## branch")
    print(f"head: {head}")
    print(f"base: {base} (비교 기준: {base_ref}, merge-base {merge_base})")
    print(f"베이스보다 앞선 커밋: {ahead}개")
    print("커밋되지 않은 변경: " + (f"{len(dirty)}개 파일 (PR에는 포함되지 않음)" if dirty else "없음"))
    key = C.issue_key_from_branch(head)
    if key:
        print(f"이슈 키: {key} (브랜치 이름 맨 앞) → PR 제목 맨 앞에 \"{key} \"이 스크립트로 자동으로 붙습니다. 제목에는 쓰지 마세요")
    else:
        print("이슈 키: 없음 (브랜치 이름이 PROJ-123 같은 키로 시작하지 않으므로 제목에 붙이지 않습니다)")

    print("\n## commits")
    print(commits)
    print("\n## commit messages (본문)")
    print(commit_bodies if commit_bodies.replace("--", "").strip() else "(본문 없음)")
    print("\n## changed files")
    print(names)
    print("\n## stat")
    print(stat)

    try:
        tpath, tsource, tlabel, tnote = C.resolve_template(root)
    except C.ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    print("\n## pr template")
    if tpath is None:
        print("없음 (references/conventions-template.md의 기본 양식을 사용)")
    else:
        shown = tlabel if tsource == "저장소" else str(tpath)
        print(f"경로: {shown}{tnote}")
        print(f"출처: {tsource}" + (" (저장소의 템플릿보다 우선)" if tsource == "사용자 설정" else ""))
        if tsource == "사용자 설정":
            repo_path, repo_label, _ = C.find_repo_template(root)
            if repo_path:
                print(f"참고: 저장소에도 템플릿({repo_label})이 있지만 사용자 설정이 우선합니다")
        print("----- 템플릿 시작 -----")
        print(tpath.read_text(encoding="utf-8", errors="replace").rstrip("\n"))
        print("----- 템플릿 끝 -----")

    print("\n## contributing (PR/커밋 관련 줄 발췌)")
    cname, clines = contributing_excerpt(root)
    if cname is None:
        print("CONTRIBUTING 문서 없음")
    else:
        print(f"경로: {cname}")
        print("\n".join(clines) if clines else "(PR/커밋 관련 줄 없음)")

    print("\n## recent merged PR titles (제목 형식과 말투 참고)")
    if args.no_gh:
        print("(--no-gh: 조회하지 않음)")
    else:
        gh_args = ["pr", "list", "--state", "merged", "--limit", "10", "--json", "number,title",
                   "--jq", '.[] | "#\\(.number) \\(.title)"']
        if args.repo:
            gh_args += ["-R", args.repo]
        r = C.gh(gh_args, cwd)
        if r.ok and r.out:
            print(r.out)
        elif r.ok:
            print("(머지된 PR이 아직 없음: 저장소의 제목 관례는 CONTRIBUTING과 기본 양식을 따름)")
        else:
            print(f"(가져오지 못함: {r.err or '알 수 없는 오류'})")

    print("\n## issue hints (브랜치 이름과 커밋에서 찾은 번호)")
    hints = issue_hints(head, commits, commit_bodies)
    print(", ".join(hints) if hints else "없음")

    print("\n## diff")
    print(diff)
    n = len(diff.splitlines())
    if n > args.max_diff_lines:
        print(f"경고: diff가 {n}줄입니다(기준 {args.max_diff_lines}줄). 설명이 흐려질 수 있으니 PR을 나누는 것을 "
              "고려하세요.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
