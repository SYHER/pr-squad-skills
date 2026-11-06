"""pr-write-squad 스크립트들이 함께 쓰는 작은 도구 모음."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 키, 토큰, 비밀번호처럼 보이는 문자열 (이름만 보고하고 값은 출력하지 않는다)
SECRET_PATTERNS = {
    "AWS 액세스 키": r"AKIA[0-9A-Z]{16}",
    "GitHub 토큰": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "Slack 토큰": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "Google API 키": r"AIza[0-9A-Za-z_\-]{30,}",
    "개인 키": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "키/토큰/비밀번호 할당": r"(?i)(api[_-]?key|secret|token|passwd|password)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]",
}

_GIT = None


def find_git():
    """실제로 실행되는 git을 찾는다.

    PATH 앞쪽에 다른 아키텍처용 git이 있어서 실행에 실패하는 환경이 있다.
    PR_WRITE_GIT, PATH의 git, 흔한 설치 경로 순으로 `--version`이 성공하는 첫 번째를 쓴다.
    """
    global _GIT
    if _GIT:
        return _GIT
    # PATH의 각 폴더를 직접 훑어 절대 경로 후보를 만든다. 'git'이라는 이름만 넘기면 파이썬은
    # 첫 후보가 실행되지 않을 때 다음 PATH 항목으로 넘어가지만 쉘은 그렇지 않아, 같은 이름이
    # 호출하는 곳마다 다른 git을 가리킬 수 있다.
    # Windows에서는 실행 파일 이름이 git.exe이므로 PATHEXT의 확장자도 함께 후보로 만든다.
    names = ["git"]
    if os.name == "nt":
        exts = [e for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(os.pathsep) if e]
        names = ["git" + e.lower() for e in exts] + names
    path_dirs = [os.path.join(d, n) for d in os.environ.get("PATH", "").split(os.pathsep) if d for n in names]
    common = ["/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git"]
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LOCALAPPDATA") and os.path.join(os.environ["LOCALAPPDATA"], "Programs")):
            if base:
                common.append(os.path.join(base, "Git", "cmd", "git.exe"))
    candidates, seen = [], set()
    for c in [os.environ.get("PR_WRITE_GIT"), *path_dirs, shutil.which("git"), *common]:
        if c and c not in seen and os.path.isfile(c) and os.access(c, os.X_OK):
            seen.add(c)
            candidates.append(c)
    for c in candidates:
        try:
            r = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            _GIT = c
            return c
    raise RuntimeError("실행되는 git을 찾을 수 없습니다")


def gh_exe():
    return os.environ.get("PR_WRITE_GH", "gh")


def gh_cmd():
    """gh 실행 명령(리스트). 테스트용 가짜 gh가 .py 파일이면 현재 파이썬으로 실행한다(Windows는 shebang을 모른다)."""
    exe = gh_exe()
    return [sys.executable, exe] if exe.lower().endswith(".py") else [exe]


def use_utf8_stdio():
    """출력에 이모지나 한글이 섞여도 Windows 콘솔(cp949 등)에서 UnicodeEncodeError로 죽지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


class Result:
    def __init__(self, returncode, out, err):
        self.returncode, self.out, self.err = returncode, out, err

    @property
    def ok(self):
        return self.returncode == 0


def _timeout():
    try:
        return max(1, int(os.environ.get("PR_WRITE_TIMEOUT", "120")))
    except ValueError:
        return 120


def run(cmd, cwd=None, stdin=None, path_first=None):
    """명령을 실행한다. 응답이 없을 때 영원히 기다리지 않도록 제한 시간(기본 120초)을 둔다.

    git 자격 증명 도우미(예: macOS 키체인)가 응답하지 않아 멈추는 환경이 있다.
    프롬프트를 띄우거나 입력을 기다리지 않게 하고(GIT_TERMINAL_PROMPT=0, stdin 차단),
    제한 시간을 넘기면 returncode 124로 돌려준다. PR_WRITE_TIMEOUT(초)로 조정한다.
    """
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if path_first:
        env["PATH"] = path_first + os.pathsep + env.get("PATH", "")
    seconds = _timeout()
    # git과 gh는 UTF-8로 출력한다. 로캘 인코딩(Windows의 cp949 등)으로 읽으면 한글 diff가 깨지거나 예외가 난다.
    kw = dict(cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
              env=env, timeout=seconds)
    if stdin is None:
        kw["stdin"] = subprocess.DEVNULL
    else:
        kw["input"] = stdin
    try:
        r = subprocess.run(cmd, **kw)
    except OSError as e:
        return Result(127, "", str(e))
    except subprocess.TimeoutExpired:
        return Result(124, "", f"{seconds}초 안에 끝나지 않아 중단했습니다 "
                               "(git 자격 증명 도우미나 네트워크가 응답하지 않을 수 있습니다)")
    return Result(r.returncode, r.stdout.rstrip("\n"), r.stderr.strip())


def git(args, cwd=None):
    return run([find_git(), *args], cwd=cwd)


def gh(args, cwd=None, stdin=None):
    # gh도 내부에서 git을 부른다. PATH 앞쪽에 실행되지 않는 git이 있으면 gh가 그것을 집어 실패하므로
    # 우리가 찾은 (실행되는) git의 폴더를 PATH 맨 앞에 둔다.
    return run([*gh_cmd(), *args], cwd=cwd, stdin=stdin, path_first=os.path.dirname(find_git()))


# ---------------------------------------------------------------------------
# 사용자 설정과 PR 템플릿 결정
# ---------------------------------------------------------------------------
TEMPLATE_FILES = [
    ".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/pull_request_template.md", "docs/PULL_REQUEST_TEMPLATE.md",
    "pull_request_template.md", "PULL_REQUEST_TEMPLATE.md",
]
TEMPLATE_DIRS = [".github/PULL_REQUEST_TEMPLATE", "docs/PULL_REQUEST_TEMPLATE", "PULL_REQUEST_TEMPLATE"]


class ConfigError(Exception):
    pass


def config_path():
    """사용자 설정 파일 위치. PR_WRITE_CONFIG > $XDG_CONFIG_HOME > ~/.config 순."""
    env = os.environ.get("PR_WRITE_CONFIG")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "pr-write-squad" / "config.json"


def load_config():
    p = config_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"설정 파일을 읽을 수 없습니다: {p} ({e})")
    if not isinstance(data, dict):
        raise ConfigError(f"설정 파일의 최상위는 객체여야 합니다: {p}")
    return data


def find_repo_template(root):
    """저장소 안의 PR 템플릿을 찾는다. (절대경로 또는 None, 저장소 기준 이름, 안내 메모)"""
    root = Path(root)
    for rel in TEMPLATE_FILES:
        if (root / rel).is_file():
            return root / rel, rel, ""
    for d in TEMPLATE_DIRS:
        folder = root / d
        if folder.is_dir():
            files = sorted(f for f in folder.iterdir() if f.suffix.lower() == ".md")
            if files:
                note = ""
                if len(files) > 1:
                    note = f" (템플릿이 {len(files)}개: {', '.join(f.name for f in files)}. 첫 번째를 보여 줌)"
                return files[0], f"{d}/{files[0].name}", note
    return None, "", ""


def resolve_template(repo_root=None, explicit=None, disabled=False):
    """사용할 PR 템플릿을 정한다. 우선순위: 직접 지정 > 사용자 설정 > 저장소 템플릿 > 없음(기본 양식).

    반환: (경로 또는 None, 출처, 저장소 기준 이름, 메모). 출처는 '지정', '사용자 설정', '저장소' 중 하나.
    사용자 설정에 적힌 파일이 없으면 다른 템플릿으로 슬쩍 대체하지 않고 ConfigError를 낸다.
    """
    if disabled:
        return None, None, "", ""
    if explicit:
        return Path(explicit), "지정", "", ""
    configured = load_config().get("template")
    if configured:
        p = Path(os.path.expanduser(str(configured)))
        if not p.is_file():
            raise ConfigError(f"설정된 PR 템플릿 파일을 찾을 수 없습니다: {p}\n"
                              "scripts/configure.py --template <경로>로 고치거나 --clear로 해제하세요")
        return p, "사용자 설정", "", ""
    if repo_root:
        path, label, note = find_repo_template(repo_root)
        if path:
            return path, "저장소", label, note
    return None, None, "", ""


def repo_root_of(cwd="."):
    r = git(["rev-parse", "--show-toplevel"], str(cwd))
    return r.out if r.ok and r.out else None


# ---------------------------------------------------------------------------
# 브랜치 이름의 이슈 키 (예: PROJ-190/feat/x → 제목 맨 앞에 'PROJ-190 ')
# ---------------------------------------------------------------------------
# 브랜치 이름 '맨 앞'이 대문자 프로젝트 코드 + 하이픈 + 숫자일 때만 키로 본다.
# fix-12, issue-5 같은 흔한 브랜치 이름이 키로 오인되지 않도록 대문자만 인정하고,
# 키 바로 뒤에는 영문자나 숫자가 오지 않아야 한다(PROJ-19a 같은 것은 키가 아님).
ISSUE_KEY_RE = re.compile(r"^([A-Z][A-Z0-9]+-\d+)(?![A-Za-z0-9])")


def issue_key_from_branch(branch):
    m = ISSUE_KEY_RE.match(branch or "")
    return m.group(1) if m else None


def compose_title(title, branch, enabled=True):
    """브랜치 이름 맨 앞의 이슈 키를 제목 맨 앞에 붙인다. (최종 제목, 키 또는 None)을 돌려준다.

    키가 없으면 제목을 그대로 둔다. 제목이 이미 그 키로 시작하면('PROJ-190 ...', '[PROJ-190] ...') 다시 붙이지 않는다.
    """
    t = (title or "").strip()
    key = issue_key_from_branch(branch) if enabled else None
    if not key:
        return t, None
    if re.match(r"^\[?" + re.escape(key) + r"(?![A-Za-z0-9])", t):
        return t, key
    return f"{key} {t}", key


def current_branch(cwd="."):
    r = git(["rev-parse", "--abbrev-ref", "HEAD"], str(cwd))
    return r.out if r.ok and r.out and r.out != "HEAD" else None
