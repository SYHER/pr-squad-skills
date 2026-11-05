"""create_pr.py 테스트: 실제 임시 git 저장소와 로컬 bare 원격, 호출을 기록하는 가짜 gh."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support as S
import validate_pr as V

SCRIPT = S.SCRIPTS / "create_pr.py"

TITLE = "feat(payment): 할인율 적용 추가"
BODY = """## 개요
할인율을 받아 합계에 적용합니다.

## 변경 이유
결제 금액에 할인이 필요했습니다.

## 이번에 하지 않은 것
- 쿠폰은 다루지 않습니다.

## 확인 방법
- 단위 테스트를 실행했고 통과했습니다.
"""
CLAIMS = [{"id": "K1", "kind": "change", "text": "할인율을 적용한다", "evidence": "src/payment.py:2",
           "verdict": "SUPPORTED", "note": ""}]
VERIF = [{"what": "단위 테스트", "command": "python3 -m unittest", "result": "passed", "evidence": "OK", "reason": ""}]


class CreatePrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work, self.remote = S.make_repo(self.tmp.name)
        S.git(self.work, "checkout", "-q", "-b", "feat/discount")
        S.commit_file(self.work, "src/payment.py", "def t(p, q):\n    return p * q\n", "feat: 합계")
        d = Path(self.tmp.name)
        (d / "title.txt").write_text(TITLE, encoding="utf-8")
        (d / "body.md").write_text(BODY, encoding="utf-8")
        (d / "claims.json").write_text(json.dumps(CLAIMS), encoding="utf-8")
        (d / "verification.json").write_text(json.dumps(VERIF), encoding="utf-8")
        self.d = d

    def tearDown(self):
        self.tmp.cleanup()

    def gh_state(self, existing=None, create_fail=None, is_draft=False):
        responses = [
            {"match": ["repo view"], "stdout": json.dumps({"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}})},
            {"match": ["pr list"], "stdout": json.dumps(existing or [])},
            {"match": ["pr view"], "stdout": json.dumps({"state": "OPEN", "isDraft": is_draft, "baseRefName": "main",
                                                         "headRefName": "feat/discount", "url": "https://github.com/o/r/pull/9"})},
            {"match": ["pr edit"], "stdout": ""},
        ]
        if create_fail:
            responses.insert(0, {"match": ["pr create"], "fail": create_fail})
        else:
            responses.insert(0, {"match": ["pr create"], "stdout": "https://github.com/o/r/pull/9\n"})
        exe, env, log = S.make_fake_gh(self.tmp.name, responses)
        self.log = log
        return env

    def run_cli(self, *extra, env=None, with_checks=True, title_file=None):
        cmd = [sys.executable, str(SCRIPT), "--repo-dir", str(self.work),
               "--title-file", str(title_file or self.d / "title.txt"), "--body-file", str(self.d / "body.md")]
        if with_checks:
            cmd += ["--claims", str(self.d / "claims.json"), "--verification", str(self.d / "verification.json")]
        return subprocess.run(cmd + list(extra), capture_output=True, text=True, env=dict(os.environ, **(env or {})))

    def remote_has(self, branch):
        return bool(S.git(self.remote, "branch", "--list", branch))

    def writes(self):
        return [c["args"] for c in S.read_calls(self.log) if c["args"][:2] in (["pr", "create"], ["pr", "edit"])]

    # ---- 미리보기가 기본 ----
    def test_dry_run_does_not_push_or_create(self):
        env = self.gh_state()
        r = self.run_cli(env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.remote_has("feat/discount"))
        self.assertIn("미리보기", r.stdout)
        self.assertIn("push: 필요", r.stdout)
        self.assertIn("새 PR 생성 (Ready for review)", r.stdout)
        self.assertIn("--post", r.stdout)

    # ---- 생성 ----
    def test_post_pushes_and_creates(self):
        env = self.gh_state()
        r = self.run_cli("--post", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.remote_has("feat/discount"))
        create = self.writes()[0]
        self.assertEqual(create[:2], ["pr", "create"])
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertEqual(create[create.index("--head") + 1], "feat/discount")
        self.assertEqual(create[create.index("--title") + 1], TITLE)
        self.assertNotIn("--draft", create)
        self.assertIn("PR 생성됨: https://github.com/o/r/pull/9", r.stdout)
        self.assertIn("확인: state=OPEN draft=False", r.stdout)

    def test_body_gets_marker_appended_after_validation(self):
        env = self.gh_state()
        self.run_cli("--post", env=env)
        body = [c["body"] for c in S.read_calls(self.log) if "body" in c][0]
        self.assertTrue(body.rstrip().endswith(V.MARKER))
        self.assertIn("## 개요", body)

    def test_draft_flag(self):
        env = self.gh_state(is_draft=True)
        r = self.run_cli("--post", "--draft", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--draft", self.writes()[0])
        self.assertIn("draft=True", r.stdout)

    def test_no_push_when_remote_is_up_to_date(self):
        S.git(self.work, "push", "-q", "-u", "origin", "feat/discount")
        env = self.gh_state()
        r = self.run_cli("--post", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("push: 필요 없음", r.stdout)
        self.assertNotIn("push 완료", r.stdout)

    def test_push_is_never_forced(self):
        S.git(self.work, "push", "-q", "-u", "origin", "feat/discount")
        # 로컬 이력을 다시 써서 원격과 갈라지게 만든다
        S.git(self.work, "commit", "-q", "--amend", "-m", "feat: 합계 (수정)")
        env = self.gh_state()
        r = self.run_cli("--post", env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("갈라져", r.stderr)
        self.assertEqual(self.writes(), [])

    # ---- 거부 ----
    def test_refuses_on_main(self):
        S.git(self.work, "checkout", "-q", "main")
        r = self.run_cli("--post", env=self.gh_state())
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.writes(), [])

    def test_refuses_protected_head_even_when_base_is_a_different_branch(self):
        """head==base 검사에 가려지지 않도록, 베이스가 다른 브랜치인 경우의 보호 브랜치 검사."""
        S.git(self.work, "branch", "stable", "main")
        S.git(self.work, "push", "-q", "origin", "stable")
        S.git(self.work, "checkout", "-q", "main")
        S.commit_file(self.work, "x.txt", "x\n", "feat: main에 직접 커밋")
        r = self.run_cli("--post", "--base", "stable", env=self.gh_state())
        self.assertEqual(r.returncode, 1)
        self.assertIn("브랜치에서는 PR을 만들지 않습니다", r.stderr)
        self.assertEqual(self.writes(), [])
        self.assertEqual(S.git(self.remote, "rev-parse", "main"), S.git(self.work, "rev-parse", "origin/main"))

    def test_refuses_when_no_commits_ahead(self):
        S.git(self.work, "checkout", "-q", "-b", "empty", "main")
        r = self.run_cli("--post", env=self.gh_state())
        self.assertEqual(r.returncode, 1)
        self.assertIn("앞선 커밋이 없어", r.stderr)

    def test_refuses_invalid_body_without_touching_github_or_remote(self):
        (self.d / "body.md").write_text(BODY + "\nTODO: 나중에\n", encoding="utf-8")
        env = self.gh_state()
        r = self.run_cli("--post", env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("PLACEHOLDER", r.stderr)
        self.assertFalse(self.remote_has("feat/discount"))
        self.assertEqual(S.read_calls(self.log), [])

    def test_refuses_unsupported_claim(self):
        bad = [dict(CLAIMS[0], verdict="UNSUPPORTED", note="코드와 다름")]
        (self.d / "claims.json").write_text(json.dumps(bad), encoding="utf-8")
        r = self.run_cli("--post", env=self.gh_state())
        self.assertEqual(r.returncode, 1)
        self.assertIn("CLAIM_NOT_SUPPORTED", r.stderr)
        self.assertFalse(self.remote_has("feat/discount"))

    def test_requires_fact_check_files_unless_explicitly_skipped(self):
        r = self.run_cli("--post", env=self.gh_state(), with_checks=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--allow-unchecked", r.stderr)
        self.assertFalse(self.remote_has("feat/discount"))
        r2 = self.run_cli("--post", "--allow-unchecked", env=self.gh_state(), with_checks=False)
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_title_pattern_is_enforced(self):
        r = self.run_cli("--post", "--title-pattern", r"^\[[A-Z]+-\d+\]", env=self.gh_state())
        self.assertEqual(r.returncode, 1)
        self.assertIn("TITLE_PATTERN", r.stderr)

    def test_secret_blocks_everything(self):
        (self.d / "body.md").write_text(BODY + "\n키 AKIA" + "Z" * 16 + "\n", encoding="utf-8")
        r = self.run_cli("--post", env=self.gh_state())
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("AKIAZZ", r.stdout + r.stderr)
        self.assertFalse(self.remote_has("feat/discount"))

    # ---- 기존 PR ----
    def test_updates_existing_pr_created_by_this_skill(self):
        env = self.gh_state(existing=[{"number": 5, "url": "https://github.com/o/r/pull/5", "isDraft": False,
                                       "body": "이전 본문\n" + V.MARKER}])
        r = self.run_cli("--post", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        w = self.writes()
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0][:3], ["pr", "edit", "5"])
        self.assertIn("PR 갱신됨", r.stdout)

    def test_does_not_overwrite_a_human_written_pr(self):
        env = self.gh_state(existing=[{"number": 5, "url": "https://github.com/o/r/pull/5", "isDraft": False,
                                       "body": "사람이 직접 쓴 본문"}])
        r = self.run_cli("--post", env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("덮어쓰지 않습니다", r.stderr)
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.remote_has("feat/discount"))

    def test_overwrite_flag_allows_it(self):
        env = self.gh_state(existing=[{"number": 5, "url": "https://github.com/o/r/pull/5", "isDraft": False,
                                       "body": "사람이 직접 쓴 본문"}])
        r = self.run_cli("--post", "--overwrite", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.writes()[0][:3], ["pr", "edit", "5"])

    # ---- 경고와 오류 처리 ----
    def test_uncommitted_changes_warn_but_do_not_block(self):
        (self.work / "wip.txt").write_text("x", encoding="utf-8")
        r = self.run_cli(env=self.gh_state())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("커밋되지 않은 변경 1개 파일은 PR에 포함되지 않음", r.stdout)

    def test_create_failure_is_reported(self):
        r = self.run_cli("--post", env=self.gh_state(create_fail="HTTP 422"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("PR 생성 실패", r.stderr)
        self.assertIn("422", r.stderr)

    def test_never_creates_commits(self):
        self.run_cli("--post", env=self.gh_state())
        log = S.git(self.work, "log", "--format=%s", "-5")
        self.assertEqual(log.splitlines(), ["feat: 합계", "chore: 초기 커밋"])  # 새 커밋 없음

    # ---- 원격 조회 실패와 응답 없음 ----
    def fake_git(self, ls_remote_body):
        """ls-remote만 가로채는 git 래퍼를 만든다(나머지는 진짜 git으로 위임)."""
        real = S.C.find_git()
        wrapper = self.d / "git_wrapper"
        wrapper.write_text(f'#!/bin/sh\nif [ "$1" = "ls-remote" ]; then\n{ls_remote_body}\nfi\nexec "{real}" "$@"\n',
                           encoding="utf-8")
        wrapper.chmod(0o755)
        return {"PR_WRITE_GIT": str(wrapper)}

    def test_failed_remote_lookup_stops_before_any_push_or_pr(self):
        env = dict(self.gh_state(), **self.fake_git('echo "fatal: unable to access" >&2; exit 128'))
        r = self.run_cli("--post", env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("조회하지 못했습니다", r.stderr)
        self.assertIn("아무것도 push하지 않음", r.stderr)
        self.assertFalse(self.remote_has("feat/discount"))
        self.assertEqual(self.writes(), [])

    def test_unresponsive_remote_times_out_instead_of_hanging(self):
        import time
        env = dict(self.gh_state(), **self.fake_git("sleep 30"), PR_WRITE_TIMEOUT="2")
        start = time.time()
        r = self.run_cli("--post", env=env)
        self.assertLess(time.time() - start, 20)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("끝나지 않아 중단했습니다", r.stderr)
        self.assertFalse(self.remote_has("feat/discount"))
        self.assertEqual(self.writes(), [])

    def test_gh_runs_with_the_working_git_first_on_path(self):
        """gh가 내부에서 실행되지 않는 git을 집어 PR 생성에 실패한 실제 사례의 회귀 테스트."""
        env = self.gh_state()
        self.run_cli("--post", env=env)
        paths = {c["path0"] for c in S.read_calls(self.log)}
        self.assertEqual(paths, {os.path.dirname(S.C.find_git())})

    def test_script_source_has_no_force_push(self):
        src = Path(SCRIPT).read_text(encoding="utf-8")
        for bad in ('"--force"', "'--force'", '"-f"', "force-with-lease", '"--delete"'):
            self.assertNotIn(bad, src)

    def test_missing_title_is_a_usage_error(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--repo-dir", str(self.work), "--body-file", str(self.d / "body.md")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
