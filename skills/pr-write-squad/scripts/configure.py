#!/usr/bin/env python3
"""PR을 작성할 때 항상 쓸 템플릿 같은 사용자 설정을 관리한다.

설정은 저장소 밖의 사용자 설정 파일(기본 ~/.config/pr-write-squad/config.json)에 저장된다.
공개 저장소에 내 컴퓨터의 경로가 들어가지 않고, 어떤 저장소에서 PR을 쓰든 같은 템플릿을 쓰게 한다.
사용자 설정 템플릿은 저장소 자체의 PR 템플릿보다 우선한다.

사용법:
  python3 configure.py --template /경로/pull_request_template.md   항상 이 템플릿으로 PR을 쓴다
  python3 configure.py --show                                     현재 설정과 템플릿의 섹션을 보여 준다
  python3 configure.py --clear                                    템플릿 설정을 해제한다(저장소 템플릿을 쓴다)

종료 코드: 0 = 성공, 1 = 지정한 템플릿을 쓸 수 없음, 2 = 설정 파일 문제
환경 변수 PR_WRITE_CONFIG로 설정 파일 위치를 바꿀 수 있다.
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

HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


def headings(text):
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def write_config(data):
    p = C.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=str(p.parent), suffix=".tmp", delete=False, encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        tmp = fh.name
    os.replace(tmp, p)


def main(argv):
    ap = argparse.ArgumentParser(description="pr-write-squad 사용자 설정")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--template", help="항상 쓸 PR 템플릿 파일 경로")
    g.add_argument("--show", action="store_true")
    g.add_argument("--clear", action="store_true")
    args = ap.parse_args(argv[1:])

    try:
        cfg = C.load_config()
    except C.ConfigError as e:
        print(e, file=sys.stderr)
        return 2

    if args.show:
        print(f"설정 파일: {C.config_path()}" + ("" if C.config_path().is_file() else " (아직 없음)"))
        t = cfg.get("template")
        if not t:
            print("항상 쓸 템플릿: 설정 없음 (저장소의 PR 템플릿이 있으면 그것을, 없으면 기본 양식을 씁니다)")
            return 0
        p = Path(os.path.expanduser(str(t)))
        print(f"항상 쓸 템플릿: {p}")
        if not p.is_file():
            print("  주의: 파일이 없습니다. PR 작성이 이 오류로 멈춥니다. --template으로 고치거나 --clear로 해제하세요.")
            return 0
        print("  섹션: " + " / ".join(headings(p.read_text(encoding="utf-8", errors="replace"))))
        return 0

    if args.clear:
        if "template" not in cfg:
            print("해제할 템플릿 설정이 없습니다.")
            return 0
        cfg.pop("template")
        if cfg:
            write_config(cfg)
        else:
            C.config_path().unlink()
        print("템플릿 설정을 해제했습니다. 이제 저장소의 PR 템플릿(없으면 기본 양식)을 씁니다.")
        return 0

    p = Path(os.path.expanduser(args.template)).resolve()
    if not p.is_file():
        print(f"템플릿 파일을 찾을 수 없습니다: {p}", file=sys.stderr)
        return 1
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"템플릿 파일을 읽을 수 없습니다: {e}", file=sys.stderr)
        return 1
    hs = headings(text)
    if not hs:
        print("템플릿에 섹션 제목(## ...)이 하나도 없어 형식을 강제할 수 없습니다. 제목이 있는 템플릿을 지정하세요.",
              file=sys.stderr)
        return 1
    cfg["template"] = str(p)
    write_config(cfg)
    print(f"설정했습니다: 이제 PR은 항상 이 템플릿으로 씁니다 (저장소 자체의 템플릿보다 우선)")
    print(f"  템플릿: {p}")
    print("  섹션: " + " / ".join(hs))
    print(f"  설정 파일: {C.config_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
