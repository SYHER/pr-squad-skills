"""테스트 공용 도구: 임시 git 저장소(+로컬 bare 원격)와 호출을 기록하는 가짜 gh."""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

# 테스트가 개발자의 실제 사용자 설정(~/.config/pr-write-squad/config.json)을 읽지 않도록,
# 어떤 모듈보다 먼저 존재하지 않는 임시 경로로 돌려놓는다. 하위 프로세스도 이 환경을 물려받는다.
ISOLATED_CONFIG = os.path.join(tempfile.mkdtemp(prefix="pr-write-test-config-"), "config.json")
os.environ["PR_WRITE_CONFIG"] = ISOLATED_CONFIG

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _common as C  # noqa: E402

FAKE_GH = """#!{py}
import json, os, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
args = sys.argv[1:]
rec = {{"args": args, "path0": os.environ.get("PATH", "").split(os.pathsep)[0]}}
if "--body-file" in args:
    rec["body"] = open(args[args.index("--body-file") + 1], encoding="utf-8").read()
with open(state["log"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + "\\n")
key = " ".join(args)
for r in state["responses"]:
    if all(m in key for m in r["match"]):
        if r.get("fail"):
            sys.stderr.write(r["fail"]); sys.exit(1)
        sys.stdout.write(r["stdout"]); sys.exit(0)
sys.stderr.write("no fake response for: " + key); sys.exit(1)
"""


def git(cwd, *args):
    r = C.git(list(args), str(cwd))
    assert r.ok, f"git {' '.join(args)} 실패: {r.err}"
    return r.out


def make_repo(tmp):
    """(작업 저장소, 원격 bare 저장소)를 만든다. main이 push되어 있다."""
    tmp = Path(tmp)
    remote, work = tmp / "remote.git", tmp / "work"
    remote.mkdir(parents=True)
    git(remote, "init", "-q", "--bare", "-b", "main")
    work.mkdir(parents=True)
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.name", "tester")
    git(work, "config", "user.email", "t@example.com")
    (work / "README.md").write_text("# demo\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "chore: 초기 커밋")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")
    return work, remote


def commit_file(work, rel, text, msg):
    p = Path(work) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", msg)


def make_fake_gh(tmp, responses):
    """가짜 gh 실행 파일을 만든다. (실행파일 경로, 환경변수 dict, 호출 기록 파일) 반환."""
    tmp = Path(tmp)
    exe, state, log = tmp / "fake_gh", tmp / "gh_state.json", tmp / "gh_calls.jsonl"
    exe.write_text(FAKE_GH.format(py=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    state.write_text(json.dumps({"log": str(log), "responses": responses}), encoding="utf-8")
    return exe, {"PR_WRITE_GH": str(exe), "FAKE_GH_STATE": str(state)}, log


def read_calls(log):
    log = Path(log)
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
