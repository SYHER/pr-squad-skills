#!/usr/bin/env python3
"""PR 제목과 본문을 결정적으로 검사한다. AI의 인상이 아니라 규칙으로 통과 여부를 정한다.

사용법:
  python3 validate_pr.py --title-file title.txt --body-file body.md
        [--claims claims.json] [--verification verification.json]
        [--template PR템플릿.md] [--title-pattern 정규식] [--max-title 72]
종료 코드: 0 = 오류 없음, 1 = 오류 있음, 2 = 입력 파일 문제
출력(JSON): {"ok": bool, "errors": [{"code","message"}], "warnings": [...]}
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

MAX_TITLE = 72
MAX_BODY = 65536
MARKER = "<!-- pr-write-squad -->"

PLACEHOLDER_PATTERNS = [
    r"\bTODO\b", r"\bTBD\b", r"\bFIXME\b", r"\{\{.*?\}\}", r"여기에\s*(작성|입력|적)",
    r"(?i)\blorem ipsum\b", r"이유 미확인", r"`파일 경로`", r"#\(Issue Number\)",
]
CLAIM_KINDS = ("change", "behavior", "test", "scope")
VERDICTS = ("SUPPORTED", "UNSUPPORTED", "UNCERTAIN")
RESULTS = ("passed", "failed", "not_run")
EVIDENCE_RE = re.compile(r"^[^\s:]+:\d+(-\d+)?$")
PASS_RE = re.compile(r"통과|\bpass(ed|es)?\b|\bgreen\b|문제\s?없이\s?(동작|실행)|성공적으로\s?(실행|통과)", re.I)
NEGATION_RE = re.compile(r"않|못|없[었음]|\bnot\b|\bno\b|\bfail|실패|미실행|skipped|\bcould ?n.?t\b", re.I)
NOT_RUN_MENTION_RE = re.compile(
    r"미실행|실행하지 (못|않)|실행 못|돌리지 (못|않)|not run|not executed|skipped|could ?n.?t run|did not run",
    re.I)
OPTIONAL_RE = re.compile(r"\((선택|옵션|optional)\)", re.I)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
WHY_RE = re.compile(r"(이유|배경|동기|왜|why|motivation|context|background)", re.I)
SCOPE_RE = re.compile(r"(범위|하지 않|안 한|out of scope|not in this|non-?goals?|제외)", re.I)
VERIF_HEADING_RE = re.compile(r"(검증|확인|테스트|test|verif)", re.I)
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s*\[([ xX])\]\s*(.*?)\s*$")
EMPTY_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*(?:\[[ xX]\])?\s*$")
LABEL_LINE_RE = re.compile(r"^\s*[-*+]\s*([A-Za-z가-힣][\w가-힣 /]{0,20}):\s*$")
CHECKLIST_HEADING_RE = re.compile(r"(checklist|체크리스트)", re.I)
# "테스트를 했습니다"처럼 테스트를 실행했다고 확인하는 체크 항목 ('테스트를 추가했다'는 해당하지 않음)
RAN_TESTS_RE = re.compile(r"테스트(를|도)?\s*(했|하였|마쳤|완료)|\btested\b", re.I)


def norm_ws(text):
    return re.sub(r"\s+", " ", text).strip()


def checkbox_items(text):
    """[(체크 여부, 라벨)]"""
    out = []
    for line in text.splitlines():
        m = CHECKBOX_RE.match(line)
        if m:
            out.append((m.group(1) in "xX", m.group(2)))
    return out


def lines_outside_code(text):
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield line


def err(code, message):
    return {"code": code, "message": message}


def strip_comments(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def parse_sections(text):
    """코드 블록 밖의 제목들로 섹션을 나눈다: [(level, heading, content)]"""
    sections, cur, in_fence = [], None, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            if cur:
                sections.append(cur)
            cur = [len(m.group(1)), m.group(2).strip(), []]
        elif cur is not None:
            cur[2].append(line)
    if cur:
        sections.append(cur)
    # 하위 제목은 상위 섹션 내용으로 합친다
    out = []
    for i, (lvl, head, lines) in enumerate(sections):
        content = list(lines)
        for lvl2, head2, lines2 in sections[i + 1:]:
            if lvl2 <= lvl:
                break
            content.append(f"{'#' * lvl2} {head2}")
            content.extend(lines2)
        out.append((lvl, head, "\n".join(content)))
    return out


def norm_heading(h):
    h = OPTIONAL_RE.sub("", h)
    return re.sub(r"[\W_]+", " ", h.lower(), flags=re.U).strip()


def is_empty_content(content):
    text = strip_comments(content)
    text = re.sub(r"[\s\-\*\+>`|]+", "", text)
    return text == ""


def check_title(title, max_title, pattern):
    errors = []
    t = title.strip()
    if not t:
        return [err("TITLE_EMPTY", "제목이 비어 있습니다")]
    if "\n" in title.strip():
        errors.append(err("TITLE_MULTILINE", "제목은 한 줄이어야 합니다"))
    if len(t) > max_title:
        errors.append(err("TITLE_TOO_LONG", f"제목이 {len(t)}자입니다(최대 {max_title}자)"))
    if t.endswith("."):
        errors.append(err("TITLE_PERIOD", "제목이 마침표로 끝납니다"))
    if any(re.search(p, t) for p in PLACEHOLDER_PATTERNS) or re.search(r"<[^>]+>", t):
        errors.append(err("TITLE_PLACEHOLDER", "제목에 자리표시자가 남아 있습니다"))
    if pattern and not re.search(pattern, t):
        errors.append(err("TITLE_PATTERN", f"제목이 저장소 형식({pattern})과 다릅니다"))
    return errors


def check_body(body, template, warnings):
    errors = []
    if not body.strip():
        return [err("BODY_EMPTY", "본문이 비어 있습니다")]
    if len(body) > MAX_BODY:
        errors.append(err("BODY_TOO_LONG", f"본문이 {len(body)}자로 GitHub 한도({MAX_BODY}자)를 넘습니다"))

    for m in re.finditer(r"<!--(.*?)-->", body, flags=re.S):
        if m.group(0).strip() != MARKER:
            errors.append(err("PLACEHOLDER", "템플릿 안내 주석(<!-- ... -->)이 남아 있습니다. 내용으로 바꾸고 지우세요"))
            break
    visible = strip_comments(body)
    for pat in PLACEHOLDER_PATTERNS:
        m = re.search(pat, visible)
        if m:
            errors.append(err("PLACEHOLDER", f"자리표시자가 남아 있습니다: '{m.group(0)}'"))

    sections = parse_sections(body)
    by_heading = {norm_heading(h): c for _, h, c in sections}
    if template:
        for _, th, _ in parse_sections(template):
            if OPTIONAL_RE.search(th):
                continue
            key = norm_heading(th)
            if key not in by_heading:
                errors.append(err("TEMPLATE_SECTION", f"템플릿 섹션 '{th}'이(가) 본문에 없습니다"))
            elif is_empty_content(by_heading[key]):
                errors.append(err("TEMPLATE_SECTION", f"템플릿 섹션 '{th}'의 내용이 비어 있습니다"))

    # 빈 목록 항목('- ', '- [ ] ')은 템플릿의 빈칸이 채워지지 않은 것이다
    if any(EMPTY_ITEM_RE.match(l) for l in lines_outside_code(visible)):
        errors.append(err("EMPTY_ITEM", "내용이 없는 목록 항목('- ' 또는 '- [ ] ')이 남아 있습니다. 채우거나 지우세요"))

    if template:
        by_body = {norm_heading(h): c for _, h, c in sections}
        for _, th, tc in parse_sections(template):
            if OPTIONAL_RE.search(th):
                continue
            bc = by_body.get(norm_heading(th))
            if bc is None:
                continue  # 위에서 TEMPLATE_SECTION으로 이미 보고함
            tvisible = strip_comments(tc)
            bvisible = strip_comments(bc)
            # 템플릿의 체크박스 선택지는 지우거나 바꾸지 않는다(해당하는 것만 체크한다)
            t_items = [lab for _, lab in checkbox_items(tvisible) if lab]
            for lab in t_items:
                if norm_ws(lab) not in norm_ws(bvisible):
                    errors.append(err("TEMPLATE_OPTION_MISSING",
                                      f"템플릿 섹션 '{th}'의 선택지가 본문에 없습니다: '{lab[:50]}'"))
            # 'What:', 'Why:' 같은 소제목 라벨도 유지한다
            labels = []
            for line in tvisible.splitlines():
                m = LABEL_LINE_RE.match(line)
                if m and m.group(1) not in labels:
                    labels.append(m.group(1))
            for lab in labels:
                if not re.search(re.escape(lab) + r"\s*:", bvisible):
                    errors.append(err("TEMPLATE_LABEL_MISSING",
                                      f"템플릿 섹션 '{th}'의 항목 '{lab}:'이(가) 본문에 없습니다"))
            b_items = checkbox_items(bvisible)
            if t_items and b_items and not any(c for c, _ in b_items):
                warnings.append(err("NO_CHECKED_ITEM", f"섹션 '{th}'에서 체크한 선택지가 하나도 없습니다"))
            if CHECKLIST_HEADING_RE.search(th):
                for checked, lab in b_items:
                    if checked and not RAN_TESTS_RE.search(lab):
                        warnings.append(err("UNVERIFIED_CHECKLIST",
                                            f"체크리스트 '{lab[:50]}'을(를) 체크했습니다. 근거를 확인한 것만 체크하세요"))

    heads = [h for _, h, _ in sections]
    # 템플릿이 있으면 템플릿이 본문의 구조를 정한다. 기본 양식을 기준으로 한 제목 경고는 소음이 되므로 내지 않는다.
    if not template:
        if not any(VERIF_HEADING_RE.search(h) for h in heads):
            warnings.append(err("NO_VERIFICATION_SECTION", "검증/테스트를 설명하는 섹션이 보이지 않습니다"))
        if not any(WHY_RE.search(h) for h in heads) and not WHY_RE.search(visible[:600]):
            warnings.append(err("NO_WHY", "변경 이유(배경)를 설명하는 부분이 보이지 않습니다"))
        if not any(SCOPE_RE.search(h) for h in heads):
            warnings.append(err("NO_SCOPE", "이번에 하지 않은 것(범위)을 설명하는 섹션이 보이지 않습니다"))
    if len(visible.strip()) < 80:
        warnings.append(err("BODY_SHORT", "본문이 매우 짧습니다"))
    return errors


def evidence_ok(evidence):
    """근거는 '파일:줄' 또는 '파일:시작-끝'이고, 여러 개는 쉼표나 세미콜론으로 구분한다.

    각 항목 뒤의 괄호 설명(예: 'src/a.py:4 (정의)')은 허용한다.
    """
    items = [i.strip() for i in re.split(r"[,;]", str(evidence)) if i.strip()]
    if not items:
        return False
    return all(EVIDENCE_RE.match(re.sub(r"\s*\([^)]*\)\s*$", "", i)) for i in items)


def check_claims(claims):
    errors = []
    if not isinstance(claims, list):
        return [err("CLAIMS_FORMAT", "claims는 배열이어야 합니다")]
    seen = set()
    for i, c in enumerate(claims):
        tag = f"주장[{i}] {c.get('id', '?') if isinstance(c, dict) else '?'}"
        if not isinstance(c, dict):
            errors.append(err("CLAIMS_FORMAT", f"{tag}: 객체가 아닙니다"))
            continue
        if not c.get("id") or c["id"] in seen:
            errors.append(err("CLAIMS_FORMAT", f"{tag}: id가 없거나 중복입니다"))
        seen.add(c.get("id"))
        if c.get("kind") not in CLAIM_KINDS:
            errors.append(err("CLAIMS_FORMAT", f"{tag}: kind는 {CLAIM_KINDS} 중 하나여야 합니다"))
        if not str(c.get("text", "")).strip():
            errors.append(err("CLAIMS_FORMAT", f"{tag}: text가 비어 있습니다"))
        v = c.get("verdict")
        if v not in VERDICTS:
            errors.append(err("CLAIM_NOT_SUPPORTED", f"{tag}: 사실 확인이 되지 않았습니다(verdict={v!r})"))
        elif v != "SUPPORTED":
            errors.append(err("CLAIM_NOT_SUPPORTED",
                              f"{tag}: {v} - {c.get('note', '').strip() or '근거 없음'}. 본문에서 고치거나 지우세요"))
        elif not evidence_ok(c.get("evidence", "")):
            errors.append(err("CLAIM_NOT_SUPPORTED",
                              f"{tag}: SUPPORTED인데 근거가 '파일:줄' 형식이 아닙니다 (여러 개는 쉼표로 구분)"))
    return errors


def check_verification(entries, body):
    errors = []
    if not isinstance(entries, list):
        return [err("VERIFICATION_FORMAT", "verification은 배열이어야 합니다")]
    passed = False
    not_run = False
    for i, e in enumerate(entries):
        tag = f"검증[{i}] {e.get('what', '?') if isinstance(e, dict) else '?'}"
        if not isinstance(e, dict) or e.get("result") not in RESULTS:
            errors.append(err("VERIFICATION_FORMAT", f"{tag}: result는 {RESULTS} 중 하나여야 합니다"))
            continue
        r = e["result"]
        if r == "passed":
            passed = True
            if not str(e.get("command", "")).strip() or not str(e.get("evidence", "")).strip():
                errors.append(err("VERIFICATION_EVIDENCE", f"{tag}: passed에는 command와 evidence(출력 일부)가 필요합니다"))
        elif r == "failed":
            if not str(e.get("command", "")).strip() or not str(e.get("evidence", "")).strip():
                errors.append(err("VERIFICATION_EVIDENCE", f"{tag}: failed에는 command와 evidence가 필요합니다"))
        else:
            not_run = True
            if not str(e.get("reason", "")).strip():
                errors.append(err("VERIFICATION_EVIDENCE", f"{tag}: not_run에는 이유(reason)가 필요합니다"))

    if not passed:
        for line in strip_comments(body).splitlines():
            if PASS_RE.search(line) and not NEGATION_RE.search(line):
                errors.append(err("UNBACKED_PASS_CLAIM",
                                  f"통과한 검증 기록이 없는데 본문이 통과를 말합니다: '{line.strip()[:80]}'"))
                break
    if not passed:
        for line in strip_comments(body).splitlines():
            m = CHECKBOX_RE.match(line)
            if m and m.group(1) in "xX" and RAN_TESTS_RE.search(m.group(2)) and not NEGATION_RE.search(m.group(2)):
                errors.append(err("UNBACKED_CHECK",
                                  f"통과한 검증 기록이 없는데 테스트를 했다는 항목을 체크했습니다: '{m.group(2)[:60]}'"))
                break
    if not_run and not NOT_RUN_MENTION_RE.search(body):
        errors.append(err("NOT_RUN_UNMENTIONED",
                          "실행하지 못한 검증이 있는데 본문이 그 사실을 말하지 않습니다"))
    return errors


def validate(title, body, template=None, claims=None, verification=None, title_pattern=None,
             max_title=MAX_TITLE):
    """(errors, warnings)를 돌려준다."""
    warnings = []
    errors = check_title(title, max_title, title_pattern)
    errors += check_body(body, template, warnings)
    if claims is None:
        warnings.append(err("NO_CLAIMS", "사실 확인(claims)이 제공되지 않았습니다"))
    else:
        errors += check_claims(claims)
    if verification is None:
        warnings.append(err("NO_VERIFICATION", "검증 기록(verification)이 제공되지 않았습니다"))
    else:
        errors += check_verification(verification, body)

    hits = sorted({name for name, pat in C.SECRET_PATTERNS.items() if re.search(pat, title + "\n" + body)})
    if hits:
        errors.append(err("SECRET", "키, 토큰, 비밀번호처럼 보이는 문자열이 있습니다: " + ", ".join(hits)))
    return errors, warnings


def _read(path, what):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"{what} 파일을 읽을 수 없음: {e}")


def _read_json(path, what):
    try:
        return json.loads(_read(path, what))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{what}가 올바른 JSON이 아님: {e}")


def main(argv):
    ap = argparse.ArgumentParser(description="PR 제목과 본문 검사")
    ap.add_argument("--title-file")
    ap.add_argument("--title")
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--claims")
    ap.add_argument("--verification")
    ap.add_argument("--template", help="지정하지 않으면 사용자 설정 > 저장소 템플릿 순으로 찾는다")
    ap.add_argument("--no-template", action="store_true", help="템플릿 구조 검사를 하지 않는다")
    ap.add_argument("--title-pattern")
    ap.add_argument("--max-title", type=int, default=MAX_TITLE)
    args = ap.parse_args(argv[1:])
    try:
        if args.title is None and not args.title_file:
            raise SystemExit("--title 또는 --title-file이 필요합니다")
        title = args.title if args.title is not None else _read(args.title_file, "제목")
        body = _read(args.body_file, "본문")
        try:
            tpath, tsource, _, _ = C.resolve_template(C.repo_root_of("."), args.template, args.no_template)
        except C.ConfigError as e:
            raise SystemExit(str(e))
        template = _read(tpath, "템플릿") if tpath else None
        claims = _read_json(args.claims, "claims") if args.claims else None
        verification = _read_json(args.verification, "verification") if args.verification else None
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    errors, warnings = validate(title.strip("\n"), body, template, claims, verification,
                                args.title_pattern, args.max_title)
    print(json.dumps({"ok": not errors, "template": {"path": str(tpath) if tpath else None, "source": tsource},
                      "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
