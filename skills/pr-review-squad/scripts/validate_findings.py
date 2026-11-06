#!/usr/bin/env python3
"""findings.json의 형식을 검사하고 최종 판정을 규칙대로 계산한다.

사용법: python3 validate_findings.py findings.json [--root 저장소경로]
종료 코드: 0 = 형식 정상, 1 = 형식 오류, 2 = 입력 파일 문제
출력: 판정, 등급별 개수, 관점별 점수(JSON)
--root: 지적한 파일, 줄 번호, 인용 코드를 저장소와 대조해 "warnings"를 출력한다.
        경고는 판정과 종료 코드에 영향을 주지 않는다.
"""
import json
import sys
from pathlib import Path

PERSPECTIVES = ("correctness", "security", "maintainability", "testing")
SEVERITIES = ("C0", "C1", "C2")
VERDICTS = ("CONFIRMED", "REJECTED", "UNCERTAIN", "NOT_VERIFIED")
REQUIRED = ("id", "perspective", "severity", "file", "line", "title",
            "evidence", "suggestion", "verdict", "verdict_reason")
# 보고서와 댓글에 그대로 들어가는 글 필드. 문자열이 아니면 render_report.py가 .strip()에서 죽는다.
TEXT_FIELDS = ("id", "file", "title", "evidence", "scenario", "suggestion", "verdict_reason")


def use_utf8_stdio():
    """보고서의 이모지와 한글이 Windows 콘솔(cp949 등)에서 UnicodeEncodeError를 내지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def check(findings):
    errors = []
    seen = set()
    for i, f in enumerate(findings):
        tag = f"[{i}] {f.get('id', '?')}" if isinstance(f, dict) else f"[{i}]"
        if not isinstance(f, dict):
            errors.append(f"{tag}: 객체가 아님")
            continue
        for k in REQUIRED:
            if k not in f or f[k] in (None, ""):
                errors.append(f"{tag}: 필수 필드 '{k}' 누락 또는 비어 있음")
        for k in TEXT_FIELDS:
            if f.get(k) not in (None, "") and not isinstance(f[k], str):
                errors.append(f"{tag}: '{k}'는 문자열이어야 함")
        fid = f.get("id") if isinstance(f.get("id"), str) else repr(f.get("id"))
        if fid in seen:
            errors.append(f"{tag}: id 중복")
        seen.add(fid)
        if f.get("perspective") not in PERSPECTIVES:
            errors.append(f"{tag}: perspective는 {PERSPECTIVES} 중 하나")
        if f.get("severity") not in SEVERITIES:
            errors.append(f"{tag}: severity는 {SEVERITIES} 중 하나")
        also = f.get("also_reported_by")
        if also is not None and (
                not isinstance(also, list)
                or any(p not in PERSPECTIVES or p == f.get("perspective") for p in also)):
            errors.append(f"{tag}: also_reported_by는 이 지적과 다른 관점 이름의 배열이어야 함")
        if f.get("verdict") not in VERDICTS:
            errors.append(f"{tag}: verdict는 {VERDICTS} 중 하나")
        line = f.get("line")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            errors.append(f"{tag}: line은 1 이상의 정수")
        # 모든 지적에 "어떤 상황에서 문제가 되는지"를 적게 한다. 보고서가 지적마다 보여 준다.
        if not f.get("scenario"):
            errors.append(f"{tag}: scenario(어떤 상황에서 어떤 문제가 생기는지) 필수")
        if f.get("severity") in ("C0", "C1") and f.get("verdict") == "NOT_VERIFIED":
            errors.append(f"{tag}: C0/C1은 검증을 거쳐야 함 (NOT_VERIFIED 불가)")
        # 테스트 부재 지적은 검증자가 호출 경로를 줄 번호로 추적(trace)해야 CONFIRMED 가능.
        if (f.get("perspective") == "testing"
                and f.get("severity") in ("C0", "C1")
                and f.get("verdict") == "CONFIRMED"):
            trace = f.get("trace")
            if (not isinstance(trace, list) or not trace
                    or not all(isinstance(t, str) and t.strip() for t in trace)):
                errors.append(
                    f"{tag}: 테스트 지적의 CONFIRMED에는 trace(호출 경로 추적, 문자열 배열)가 필요함")
    return errors


def decide(findings):
    confirmed = [f for f in findings if f["verdict"] == "CONFIRMED"]
    c0 = [f for f in confirmed if f["severity"] == "C0"]
    c1 = [f for f in confirmed if f["severity"] == "C1"]
    if c0:
        return "REQUEST_CHANGES"
    if c1:
        return "COMMENT"
    return "APPROVE"


def score(findings, perspective):
    """rubric.md의 관점별 점수 표를 그대로 구현."""
    mine = [f for f in findings
            if f["perspective"] == perspective and f["verdict"] == "CONFIRMED"]
    if any(f["severity"] == "C0" for f in mine):
        return 4
    c1 = sum(1 for f in mine if f["severity"] == "C1")
    if c1 >= 2:
        return 6
    if c1 == 1:
        return 8
    return 10


NEAR = 10          # 인용 코드가 지적한 줄에서 이 줄 수 안에 있으면 일치로 본다
MIN_SEGMENT = 6    # 이보다 짧은 인용 조각은 우연히 일치하기 쉬워 대조하지 않는다


def _segments(evidence):
    """리뷰어가 '...'나 ' / '로 이어 붙인 인용을 조각으로 나눈다."""
    parts = []
    for chunk in evidence.replace(" / ", "\n").replace("...", "\n").split("\n"):
        chunk = " ".join(chunk.split())
        if len(chunk) >= MIN_SEGMENT:
            parts.append(chunk)
    return parts


def check_sources(findings, root):
    """지적한 파일, 줄 번호, 인용 코드를 실제 저장소와 대조해 경고 목록을 만든다.

    형식 오류가 아니므로 판정과 종료 코드에는 영향을 주지 않는다.
    """
    warnings = []
    root = Path(root)
    cache = {}
    for f in findings:
        if f.get("verdict") == "REJECTED":
            continue
        rel = f["file"]
        path = (root / rel).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            warnings.append(f"{f['id']}: 저장소 밖 경로는 대조하지 않음 ({rel})")
            continue
        if rel not in cache:
            try:
                cache[rel] = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                cache[rel] = None
        lines = cache[rel]
        if lines is None:
            warnings.append(f"{f['id']}: 파일을 읽을 수 없음 ({rel})")
            continue
        if f["line"] > len(lines):
            warnings.append(
                f"{f['id']}: {rel}은 {len(lines)}줄인데 {f['line']}줄을 가리킴")
            continue
        segs = _segments(f["evidence"])
        if not segs:
            continue
        normalized = [" ".join(l.split()) for l in lines]
        hits = [i + 1 for seg in segs for i, l in enumerate(normalized) if seg in l]
        if not hits:
            warnings.append(f"{f['id']}: 인용한 코드를 {rel}에서 찾지 못함")
        elif min(abs(h - f["line"]) for h in hits) > NEAR:
            nearest = min(hits, key=lambda h: abs(h - f["line"]))
            warnings.append(
                f"{f['id']}: 인용한 코드가 {rel}:{nearest}에 있는데 {f['line']}줄을 가리킴")
    return warnings


def main(argv):
    use_utf8_stdio()
    args = argv[1:]
    root = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            print(__doc__)
            return 2
        root = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        with open(args[0], encoding="utf-8") as fh:
            findings = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"입력 파일을 읽을 수 없음: {e}", file=sys.stderr)
        return 2
    if not isinstance(findings, list):
        print("최상위는 배열이어야 함", file=sys.stderr)
        return 2

    errors = check(findings)
    if errors:
        print("형식 오류:")
        for e in errors:
            print(f"  - {e}")
        return 1

    reported = [f for f in findings if f["verdict"] != "REJECTED"]
    result = {
        "verdict": decide(findings),
        # C0/C1은 검증을 통과(CONFIRMED)한 것만, C2는 검증 대상이 아니므로
        # 반려되지 않은 것 전체를 센다.
        "counts": {
            s: sum(1 for f in reported
                   if f["severity"] == s
                   and (f["verdict"] != "UNCERTAIN" if s == "C2"
                        else f["verdict"] == "CONFIRMED"))
            for s in SEVERITIES
        },
        "uncertain": [f["id"] for f in findings if f["verdict"] == "UNCERTAIN"],
        "rejected": [f["id"] for f in findings if f["verdict"] == "REJECTED"],
        "scores": {p: score(findings, p) for p in PERSPECTIVES},
    }
    if root is not None:
        result["warnings"] = check_sources(findings, root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
