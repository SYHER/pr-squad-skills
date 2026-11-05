#!/usr/bin/env python3
"""리뷰 결과를 GitHub PR에 댓글로 게시한다. 기본은 미리보기(dry-run)이고 --post를 줘야 실제로 올린다.

사용법:
  python3 post_review.py findings.json --pr 123 -R owner/repo [--not-reviewed "항목" ...]
                         [--summary-only] [--max-inline 20] [--post]
  python3 post_review.py --retract --pr 123 -R owner/repo [--yes]   # 올린 댓글 철회

게시 내용:
  1. 인라인 댓글: 검증을 통과한 '꼭 고쳐야 할 문제'와 '고치는 것을 권장' 지적을 해당 코드 줄에
     하나의 리뷰(event=COMMENT)로 남긴다. diff에 없는 줄은 달 수 없으므로 요약 댓글에만 둔다.
  2. 요약 댓글: render_report.py 보고서 전체를 PR 대화에 남긴다. 이미 있으면 새로 달지 않고 갱신한다.

안전장치:
  - 승인(APPROVE)이나 변경 요청(REQUEST_CHANGES)은 제출하지 않는다. 일반 댓글(COMMENT)만 쓴다.
  - 열린(OPEN) PR에만 게시한다.
  - 이미 단 인라인 지적은 숨은 표식으로 알아보고 건너뛴다(재실행해도 중복되지 않음).
  - 키, 토큰, 비밀번호처럼 보이는 문자열이 있으면 게시를 거부한다.
  - 보고서가 GitHub 한도(65,536자)를 넘으면 게시를 거부한다.
종료 코드: 0 = 성공(미리보기 포함), 1 = 검증 또는 안전장치로 거부, 2 = 입력/gh 호출 오류
환경 변수 PR_REVIEW_GH: gh 실행 파일 경로(테스트용). 기본값 gh.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vf = _load("validate_findings")
rr = _load("render_report")

SUMMARY_MARKER = "<!-- pr-review-squad:summary -->"
COMMENT_LIMIT = 65536
DEFAULT_MAX_INLINE = 20

SECRET_PATTERNS = {
    "AWS 액세스 키": r"AKIA[0-9A-Z]{16}",
    "GitHub 토큰": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "Slack 토큰": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "Google API 키": r"AIza[0-9A-Za-z_\-]{30,}",
    "개인 키": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "키/토큰/비밀번호 할당": r"(?i)(api[_-]?key|secret|token|passwd|password)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]",
}


class GhError(Exception):
    pass


def gh(args, stdin=None):
    exe = os.environ.get("PR_REVIEW_GH", "gh")
    try:
        r = subprocess.run([exe, *args], input=stdin, capture_output=True, text=True)
    except FileNotFoundError:
        raise GhError("gh CLI를 찾을 수 없습니다")
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()
        raise GhError(f"gh {' '.join(args[:3])} 실패: {detail}")
    return r.stdout


def parse_stream(text):
    """gh --paginate --jq 출력처럼 이어 붙은 JSON 값들을 모두 읽는다."""
    dec = json.JSONDecoder()
    out, i, text = [], 0, text.strip()
    while i < len(text):
        obj, j = dec.raw_decode(text, i)
        out.append(obj)
        i = j
        while i < len(text) and text[i].isspace():
            i += 1
    return out


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_commentable(diff):
    """통합 diff에서 파일별로 댓글을 달 수 있는 새 파일 줄 번호 집합을 만든다(RIGHT 쪽).

    GitHub는 diff에 나타나는 줄(추가된 줄과 주변 문맥 줄)에만 인라인 댓글을 허용한다.
    """
    result, path = {}, None
    lines = diff.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            target = line[4:].split("\t")[0].strip()
            if target == "/dev/null":
                path = None
            else:
                if target.startswith('"') and target.endswith('"'):
                    target = target[1:-1]
                path = target[2:] if target.startswith("b/") else target
                result.setdefault(path, set())
        elif line.startswith("@@") and path is not None:
            m = HUNK.match(line)
            if m:
                old_left = int(m.group(2)) if m.group(2) is not None else 1
                new_left = int(m.group(4)) if m.group(4) is not None else 1
                new_no = int(m.group(3))
                i += 1
                while i < len(lines) and (old_left > 0 or new_left > 0):
                    body = lines[i]
                    if body.startswith("\\"):
                        i += 1
                        continue
                    tag = body[:1]
                    if tag == "+":
                        result[path].add(new_no)
                        new_no += 1
                        new_left -= 1
                    elif tag == "-":
                        old_left -= 1
                    else:  # 문맥 줄
                        result[path].add(new_no)
                        new_no += 1
                        new_left -= 1
                        old_left -= 1
                    i += 1
                continue
        i += 1
    return result


def finding_key(f):
    return hashlib.sha1(f"{f['file']}\n{f['title']}".encode("utf-8")).hexdigest()[:8]


def inline_marker(f):
    return f"<!-- pr-review-squad:{f['id']}:{finding_key(f)} -->"


def inline_body(f):
    emoji, name = rr.SECTION[f["severity"]]
    return "\n".join([
        inline_marker(f),
        f"{emoji} **{name}** — {f['title']}",
        "",
        "**어떤 상황에서 문제가 되나요?**  ",
        f["scenario"].strip(),
        "",
        "**어떻게 고치면 좋을까요?**  ",
        f["suggestion"].strip(),
        "",
        rr.verification_line(f),
        "",
        f"<sub>AI 리뷰 · 지적 번호 {f['id']} · 전체 보고서는 이 PR 대화의 요약 댓글을 참고하세요.</sub>",
    ])


def make_plan(findings, commentable, existing_markers, max_inline):
    """어떤 지적을 어디에 달지 정한다. 코드 줄에 달 수 없는 것은 이유와 함께 분리한다."""
    candidates = [f for f in findings
                  if f["verdict"] == "CONFIRMED" and f["severity"] in ("C0", "C1")]
    candidates.sort(key=lambda f: f["severity"])  # 꼭 고쳐야 할 문제를 먼저
    plan = {"post": [], "already": [], "outside": [], "over_limit": []}
    for f in candidates:
        if inline_marker(f) in existing_markers:
            plan["already"].append(f)
        elif f["line"] not in commentable.get(f["file"], set()):
            plan["outside"].append(f)
        elif len(plan["post"]) >= max_inline:
            plan["over_limit"].append(f)
        else:
            plan["post"].append(f)
    return plan


def scan_secrets(bodies):
    hits = set()
    for body in bodies:
        for name, pat in SECRET_PATTERNS.items():
            if re.search(pat, body):
                hits.add(name)
    return sorted(hits)


def build_summary(findings, not_reviewed, plan, summary_only):
    posted = len(plan["post"]) + len(plan["already"])
    note = ""
    if not summary_only:
        not_inline = len(plan["outside"]) + len(plan["over_limit"])
        note = (f"> 🔴🟠 지적 중 **{posted}건**은 해당 코드 줄에도 댓글로 남겼습니다.")
        if not_inline:
            note += (f" 코드 줄에 달 수 없는 **{not_inline}건**"
                     "(이번 PR의 변경 범위 밖이거나 한 번에 달 수 있는 개수를 넘음)은 이 댓글에만 있습니다.")
        note += "\n\n"
    return SUMMARY_MARKER + "\n" + rr.render(findings, not_reviewed, note=note)


def describe(f):
    return f"{f['id']} {f['file']}:{f['line']} {f['title']}"


def retract(repo, pr, do_it):
    """이 스킬이 올린 댓글(숨은 표식이 있는 내 댓글)만 찾아 지운다. 남의 댓글은 건드리지 않는다."""
    try:
        me = gh(["api", "user", "--jq", ".login"]).strip()
        jq = ".[] | {id: .id, login: .user.login, body: .body}"
        inline = [c for c in parse_stream(gh(
            ["api", f"repos/{repo}/pulls/{pr}/comments", "--paginate", "--jq", jq]))
            if c["login"] == me and "<!-- pr-review-squad:" in c["body"]]
        summary = [c for c in parse_stream(gh(
            ["api", f"repos/{repo}/issues/{pr}/comments", "--paginate", "--jq", jq]))
            if c["login"] == me and SUMMARY_MARKER in c["body"]]
    except (GhError, json.JSONDecodeError, KeyError) as e:
        print(f"GitHub 조회 실패: {e}", file=sys.stderr)
        return 2
    mode = "철회" if do_it else "철회 미리보기(아무것도 지우지 않음)"
    print(f"== {mode}: {repo}#{pr}, 계정 {me}")
    print(f"- 지울 인라인 댓글: {len(inline)}건, 요약 댓글: {len(summary)}건 (이 스킬이 올린 내 댓글만)")
    if not (inline or summary):
        print("지울 댓글이 없습니다.")
        return 0
    if not do_it:
        print("\n실제로 지우려면 같은 명령에 --yes를 붙이세요.")
        return 0
    try:
        for c in inline:
            gh(["api", f"repos/{repo}/pulls/comments/{c['id']}", "--method", "DELETE"])
        for c in summary:
            gh(["api", f"repos/{repo}/issues/comments/{c['id']}", "--method", "DELETE"])
    except GhError as e:
        print(f"삭제 실패: {e}", file=sys.stderr)
        return 2
    print("삭제했습니다. 리뷰 기록(COMMENTED)과 이미 나간 알림은 남을 수 있습니다.")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description="리뷰 결과를 GitHub PR에 댓글로 게시(기본: 미리보기)")
    ap.add_argument("findings", nargs="?", help="findings.json (--retract일 때는 생략)")
    ap.add_argument("--retract", action="store_true",
                    help="이 스킬이 올린 내 댓글을 지운다(기본: 미리보기, --yes를 줘야 실제로 지움)")
    ap.add_argument("--yes", action="store_true", help="--retract를 실제로 실행한다")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("-R", "--repo", required=True, help="owner/repo")
    ap.add_argument("--not-reviewed", action="append", default=[], metavar="항목")
    ap.add_argument("--summary-only", action="store_true", help="인라인 댓글 없이 요약 댓글만")
    ap.add_argument("--max-inline", type=int, default=DEFAULT_MAX_INLINE)
    ap.add_argument("--post", action="store_true", help="실제로 게시한다(없으면 미리보기)")
    args = ap.parse_args(argv[1:])

    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        print("저장소는 owner/repo 형식이어야 합니다", file=sys.stderr)
        return 2
    if args.retract:
        return retract(args.repo, str(args.pr), args.yes)
    if not args.findings:
        print("findings.json 경로가 필요합니다", file=sys.stderr)
        return 2
    try:
        with open(args.findings, encoding="utf-8") as fh:
            findings = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"입력 파일을 읽을 수 없음: {e}", file=sys.stderr)
        return 2
    if not isinstance(findings, list):
        print("최상위는 배열이어야 함", file=sys.stderr)
        return 2
    errors = vf.check(findings)
    if errors:
        print("형식 오류가 있어 게시하지 않습니다:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    repo, pr = args.repo, str(args.pr)
    try:
        info = json.loads(gh(["pr", "view", pr, "-R", repo, "--json", "state,headRefOid,url"]))
        if info.get("state") != "OPEN":
            print(f"열린 PR이 아니어서 게시하지 않습니다 (상태: {info.get('state')})", file=sys.stderr)
            return 1
        head = info["headRefOid"]
        me = gh(["api", "user", "--jq", ".login"]).strip()
        jq = ".[] | {id: .id, login: .user.login, body: .body}"
        mine_inline = [c for c in parse_stream(gh(
            ["api", f"repos/{repo}/pulls/{pr}/comments", "--paginate", "--jq", jq]))
            if c["login"] == me]
        mine_issue = [c for c in parse_stream(gh(
            ["api", f"repos/{repo}/issues/{pr}/comments", "--paginate", "--jq", jq]))
            if c["login"] == me]
        diff = gh(["pr", "diff", pr, "-R", repo]) if not args.summary_only else ""
    except (GhError, json.JSONDecodeError, KeyError) as e:
        print(f"GitHub 조회 실패: {e}", file=sys.stderr)
        return 2

    existing_markers = set()
    for c in mine_inline:
        existing_markers.update(re.findall(r"<!-- pr-review-squad:[^>]*-->", c["body"]))
    existing_summary = next((c for c in mine_issue if SUMMARY_MARKER in c["body"]), None)

    if args.summary_only:
        plan = {"post": [], "already": [], "outside": [], "over_limit": []}
    else:
        plan = make_plan(findings, parse_commentable(diff), existing_markers, args.max_inline)

    summary = build_summary(findings, args.not_reviewed, plan, args.summary_only)
    bodies = [summary] + [inline_body(f) for f in plan["post"]]

    hits = scan_secrets(bodies)
    if hits:
        print("키, 토큰, 비밀번호처럼 보이는 문자열이 있어 게시하지 않습니다: " + ", ".join(hits)
              + "\nfindings.json의 evidence를 [REDACTED]로 바꾼 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    if len(summary) > COMMENT_LIMIT:
        print(f"요약 댓글이 {len(summary)}자로 GitHub 한도({COMMENT_LIMIT}자)를 넘어 게시하지 않습니다. "
              "지적을 줄이거나 나눠서 올리세요.", file=sys.stderr)
        return 1

    mode = "게시" if args.post else "미리보기(dry-run, 아무것도 올리지 않음)"
    print(f"== {mode}: {repo}#{pr} (OPEN, head {head[:7]}), 계정 {me}")
    print(f"- 요약 댓글: {'기존 댓글을 갱신' if existing_summary else '새로 작성'} ({len(summary)}자)")
    if args.summary_only:
        print("- 인라인 댓글: 사용 안 함(--summary-only)")
    else:
        print(f"- 인라인 댓글: {len(plan['post'])}건 작성")
        for f in plan["post"]:
            print(f"    · {describe(f)}")
        for label, key in (("이미 게시되어 건너뜀", "already"),
                           ("diff에 없는 줄이라 요약에만 표시", "outside"),
                           ("개수 한도(--max-inline) 초과로 요약에만 표시", "over_limit")):
            if plan[key]:
                print(f"- {label}: {len(plan[key])}건")
                for f in plan[key]:
                    print(f"    · {describe(f)}")
    print("- 리뷰 종류: 댓글(COMMENT)만. 승인이나 변경 요청은 제출하지 않습니다.")

    if not args.post:
        print("\n실제로 올리려면 같은 명령에 --post를 붙이세요.")
        return 0

    try:
        if plan["post"]:
            payload = {
                "commit_id": head,
                "event": "COMMENT",
                "body": f"AI 리뷰: 코드 줄에 남긴 댓글 {len(plan['post'])}건입니다. "
                        "전체 보고서는 이 PR 대화의 요약 댓글을 참고하세요.",
                "comments": [{"path": f["file"], "line": f["line"], "side": "RIGHT",
                              "body": inline_body(f)} for f in plan["post"]],
            }
            resp = json.loads(gh(["api", f"repos/{repo}/pulls/{pr}/reviews",
                                  "--method", "POST", "--input", "-"], stdin=json.dumps(payload)))
            print(f"인라인 리뷰 게시됨: {resp.get('html_url', '')}")
        body = json.dumps({"body": summary})
        if existing_summary:
            resp = json.loads(gh(["api", f"repos/{repo}/issues/comments/{existing_summary['id']}",
                                  "--method", "PATCH", "--input", "-"], stdin=body))
            print(f"요약 댓글 갱신됨: {resp.get('html_url', '')}")
        else:
            resp = json.loads(gh(["api", f"repos/{repo}/issues/{pr}/comments",
                                  "--method", "POST", "--input", "-"], stdin=body))
            print(f"요약 댓글 게시됨: {resp.get('html_url', '')}")
    except (GhError, json.JSONDecodeError) as e:
        print(f"게시 실패: {e}\n요약 댓글은 인라인 리뷰가 성공해야 올리므로, 실패한 단계 이후는 실행하지 않았습니다.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
