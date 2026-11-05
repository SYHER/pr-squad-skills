"""pr-write-squad 스크립트들이 함께 쓰는 작은 도구 모음."""
import os
import subprocess

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
    path_dirs = [os.path.join(d, "git") for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    candidates, seen = [], set()
    for c in [os.environ.get("PR_WRITE_GIT"), *path_dirs, "/usr/bin/git",
              "/opt/homebrew/bin/git", "/usr/local/bin/git"]:
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
    kw = dict(cwd=cwd, capture_output=True, text=True, env=env, timeout=seconds)
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
    return run([gh_exe(), *args], cwd=cwd, stdin=stdin, path_first=os.path.dirname(find_git()))
