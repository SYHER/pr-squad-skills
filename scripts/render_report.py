#!/usr/bin/env python3
"""findings.json에서 GitHub 마크다운 리뷰 보고서를 만든다.

사용법:
  python3 render_report.py findings.json [--not-reviewed "항목" ...] [--root 저장소경로]

- 판정, 점수, 개수는 validate_findings.py와 같은 규칙으로 계산한다.
- 사용자에게 보이는 글에는 C0/C1/C2 같은 내부 등급 코드를 쓰지 않는다.
- --not-reviewed: "이번 리뷰가 확인하지 못한 것" 항목. 여러 번 줄 수 있다.
- 형식 오류가 있으면 보고서를 만들지 않고 오류를 stderr로 내고 종료 코드 1을 반환한다.
- GitHub 댓글 한도(65,536자)에 가까워지면 stderr로 경고한다.
- 이 스크립트는 출력만 한다. PR에 댓글을 달지 않는다.
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "validate_findings", Path(__file__).resolve().parent / "validate_findings.py")
vf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vf)

PERSPECTIVE = {
    "correctness": "정확성",
    "security": "보안",
    "maintainability": "가독성·유지보수",
    "testing": "테스트",
}

# 내부 등급 → 사용자에게 보이는 이름
SECTION = {
    "C0": ("🔴", "머지 전에 꼭 고쳐야 할 문제"),
    "C1": ("🟠", "고치는 것을 권장"),
    "C2": ("🟡", "참고·제안"),
}

VERDICT = {
    "REQUEST_CHANGES": ("⛔", "머지 전에 수정이 필요합니다",
                        "꼭 고쳐야 할 문제가 확인되었습니다."),
    "COMMENT": ("💬", "수정을 권장하는 사항이 있습니다",
                "머지를 막을 문제는 확인되지 않았지만, 고치는 것을 권장하는 사항이 있습니다."),
    "APPROVE": ("✅", "확인된 문제가 없습니다",
                "꼭 고쳐야 할 문제와 고치는 것을 권장하는 사항은 확인되지 않았습니다."),
}

GITHUB_COMMENT_LIMIT = 65536
WARN_AT = 60000


def fence_for(text):
    longest = max((len(r) for r in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def verification_line(f):
    reason = f.get("verdict_reason", "").strip()
    if f["verdict"] == "CONFIRMED":
        return f"✅ **확인됨** — 작성자와 다른 검증자가 코드로 확인했습니다. {reason}".strip()
    if f["verdict"] == "UNCERTAIN":
        return f"❓ **판단 보류** — {reason}".strip()
    return "ℹ️ 참고 사항이라 별도 검증은 하지 않았습니다."


def render_finding(n, f, show_level=False):
    ev = f["evidence"].strip("\n")
    fe = fence_for(ev)
    meta = f"`{f['file']}:{f['line']}` · {PERSPECTIVE[f['perspective']]} 관점 · 지적 번호 {f['id']}"
    if show_level:
        meta += f" · 처음 판단한 등급: {SECTION[f['severity']][1]}"
    also = [PERSPECTIVE[p] for p in f.get("also_reported_by") or [] if p in PERSPECTIVE]
    lines = [
        f"#### {n}. {f['title']}",
        meta,
    ]
    if also:
        lines += ["", f"같은 문제를 {', '.join(also)} 관점의 리뷰어도 지적했습니다."]
    lines += [
        "",
        "**근거 코드**",
        fe,
        ev,
        fe,
        "",
        f"**어떤 상황에서 문제가 되나요?**  \n{f['scenario'].strip()}",
        "",
        f"**어떻게 고치면 좋을까요?**  \n{f['suggestion'].strip()}",
        "",
        verification_line(f),
    ]
    trace = f.get("trace")
    if isinstance(trace, list) and trace:
        lines += ["", "<details><summary>검증자가 따라간 경로</summary>", ""]
        lines += [f"- {t}" for t in trace]
        lines += ["", "</details>"]
    return "\n".join(lines)


def count_cell(findings, perspective):
    cnt = {"C0": 0, "C1": 0, "C2": 0}
    for f in findings:
        if f["perspective"] != perspective or f["verdict"] in ("REJECTED", "UNCERTAIN"):
            continue
        if f["severity"] == "C2" or f["verdict"] == "CONFIRMED":
            cnt[f["severity"]] += 1
    parts = []
    for sev, word in (("C0", "꼭 고쳐야 함"), ("C1", "권장"), ("C2", "참고")):
        if cnt[sev]:
            parts.append(f"{word} {cnt[sev]}")
    return " · ".join(parts) if parts else "-"


def render(findings, not_reviewed, note=""):
    shown = [f for f in findings if f["verdict"] != "REJECTED"]
    uncertain = [f for f in shown if f["verdict"] == "UNCERTAIN"]
    by_level = {
        "C0": [f for f in shown if f["severity"] == "C0" and f["verdict"] == "CONFIRMED"],
        "C1": [f for f in shown if f["severity"] == "C1" and f["verdict"] == "CONFIRMED"],
        "C2": [f for f in shown if f["severity"] == "C2" and f["verdict"] != "UNCERTAIN"],
    }
    rejected = [f for f in findings if f["verdict"] == "REJECTED"]
    decision = vf.decide(findings)
    icon, headline, sentence = VERDICT[decision]

    out = [
        f"## 🔍 PR 리뷰 결과 — {icon} {headline}",
        "",
        "> AI 리뷰어 여러 명이 관점별로 검토하고, 별도의 검증자가 꼭 고쳐야 할 문제와 권장 사항을 코드로 다시 확인했습니다. 사람의 리뷰를 대체하지 않습니다.",
        "",
        f"**{sentence}**",
        "",
        f"꼭 고쳐야 할 문제 **{len(by_level['C0'])}건** · 고치는 것을 권장 **{len(by_level['C1'])}건** · "
        f"참고·제안 **{len(by_level['C2'])}건** · 확인 필요 **{len(uncertain)}건** "
        f"(검증에서 반려된 지적 {len(rejected)}건은 제외)",
        "",
    ]
    if note:
        out += [note.strip(), ""]
    out += [
        "| 관점 | 점수 | 확인된 지적 |",
        "|---|---|---|",
    ]
    for p in vf.PERSPECTIVES:
        out.append(f"| {PERSPECTIVE[p]} | {vf.score(findings, p)}/10 | {count_cell(findings, p)} |")
    out += [
        "",
        "점수는 관점별 10점 만점의 요약입니다. 최종 판정은 점수가 아니라 "
        "**꼭 고쳐야 할 문제와 권장 사항이 있는지**로 정합니다.",
        "",
    ]

    for sev in ("C0", "C1", "C2"):
        emoji, name = SECTION[sev]
        items = by_level[sev]
        out += [f"### {emoji} {name} ({len(items)}건)", ""]
        if not items:
            out += ["없음", ""]
            continue
        for n, f in enumerate(items, 1):
            out += [render_finding(n, f), ""]

    out += [f"### ❓ 확인이 필요한 항목 ({len(uncertain)}건)", ""]
    if not uncertain:
        out += ["없음", ""]
    else:
        out += ["코드만으로는 문제인지 판단하지 못한 항목입니다. 위 건수와 판정에는 넣지 않았습니다.", ""]
        for n, f in enumerate(uncertain, 1):
            out += [render_finding(n, f, show_level=True), ""]

    out += ["### 이번 리뷰가 확인하지 못한 것", ""]
    if not_reviewed:
        out += [f"- {item}" for item in not_reviewed]
    else:
        out += ["- 별도로 기록된 항목이 없습니다."]
    out.append("")
    return "\n".join(out)


def main(argv):
    ap = argparse.ArgumentParser(description="findings.json → GitHub 마크다운 리뷰 보고서")
    ap.add_argument("findings")
    ap.add_argument("--not-reviewed", action="append", default=[], metavar="항목")
    args = ap.parse_args(argv[1:])

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
        print("형식 오류가 있어 보고서를 만들지 않았습니다:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    text = render(findings, args.not_reviewed)
    if len(text) > WARN_AT:
        print(f"경고: 보고서가 {len(text)}자입니다. GitHub 댓글 한도는 {GITHUB_COMMENT_LIMIT}자이니 "
              "여러 댓글로 나누거나 지적을 줄여야 합니다.", file=sys.stderr)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
