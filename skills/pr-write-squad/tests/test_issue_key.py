"""브랜치 이름 맨 앞의 이슈 키(PROJ-190 등)를 PR 제목 맨 앞에 붙이는 규칙 테스트."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support as S
import _common as C

VALIDATE = S.SCRIPTS / "validate_pr.py"
CREATE = S.SCRIPTS / "create_pr.py"
COLLECT = S.SCRIPTS / "collect_context.py"

TITLE = "할인율 적용을 추가한다"
BODY = """## 개요
할인율을 적용합니다. 자세한 내용은 아래와 같습니다.

## 변경 이유
결제 금액에 할인이 필요했습니다.

## 이번에 하지 않은 것
- 쿠폰은 다루지 않습니다.

## 확인 방법
- 단위 테스트를 실행했고 통과했습니다.
"""
CLAIMS = [{"id": "K1", "kind": "change", "text": "할인율을 적용한다", "evidence": "src/payment.py:2",
           "verdict": "SUPPORTED", "note": ""}]
VERIF = [{"what": "테스트", "command": "t", "result": "passed", "evidence": "OK", "reason": ""}]


class IssueKeyFromBranchTests(unittest.TestCase):
    def test_key_at_the_very_front_is_found(self):
        for branch, key in [("PROJ-190/feat/add-discount", "PROJ-190"), ("PROJ-190-feat-x", "PROJ-190"),
                            ("PROJ-190_x", "PROJ-190"), ("PROJ-190", "PROJ-190"), ("AB-1/x", "AB-1"),
                            ("ABC2-45/fix", "ABC2-45"), ("PROJ-1234/a/b", "PROJ-1234")]:
            with self.subTest(branch=branch):
                self.assertEqual(C.issue_key_from_branch(branch), key)

    def test_key_not_at_the_front_is_ignored(self):
        for branch in ("feat/PROJ-190-x", "feature/PROJ-190", "bugfix-PROJ-190", "x/PROJ-190", "my-PROJ-190"):
            with self.subTest(branch=branch):
                self.assertIsNone(C.issue_key_from_branch(branch))

    def test_ordinary_branch_names_are_not_mistaken_for_keys(self):
        for branch in ("fix-12/login", "issue-5", "feat-2/y", "proj-190/x", "hotfix-1", "bug-3/z",
                       "main", "develop", "release/1.2", "PROJ190/x", "PROJ-/x", "A-1/x", "PROJ-19a/x", "PROJ-190x"):
            with self.subTest(branch=branch):
                self.assertIsNone(C.issue_key_from_branch(branch))

    def test_empty_values(self):
        self.assertIsNone(C.issue_key_from_branch(""))
        self.assertIsNone(C.issue_key_from_branch(None))


class ComposeTitleTests(unittest.TestCase):
    def test_key_is_prefixed_with_a_single_space_like_the_team_convention(self):
        self.assertEqual(C.compose_title(TITLE, "PROJ-190/feat/add-discount"),
                         ("PROJ-190 할인율 적용을 추가한다", "PROJ-190"))

    def test_nothing_is_added_without_a_key(self):
        for branch in ("feat/safe-order-query", "feat/PROJ-190-x", "main", "", None):
            with self.subTest(branch=branch):
                self.assertEqual(C.compose_title(TITLE, branch), (TITLE, None))

    def test_key_is_not_doubled_when_the_title_already_has_it(self):
        for title in ("PROJ-190 제목", "[PROJ-190] 제목", "PROJ-190: 제목", "PROJ-190"):
            with self.subTest(title=title):
                self.assertEqual(C.compose_title(title, "PROJ-190/x"), (title, "PROJ-190"))

    def test_a_longer_number_with_the_same_start_is_not_treated_as_present(self):
        self.assertEqual(C.compose_title("PROJ-1900 제목", "PROJ-190/x")[0], "PROJ-190 PROJ-1900 제목")

    def test_can_be_disabled(self):
        self.assertEqual(C.compose_title(TITLE, "PROJ-190/x", enabled=False), (TITLE, None))

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(C.compose_title("  제목\n", "PROJ-1/x")[0], "PROJ-1 제목")


class GitFixtureBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.work, self.remote = S.make_repo(self.dir / "git")
        S.git(self.work, "checkout", "-q", "-b", "PROJ-7/feat/discount")
        S.commit_file(self.work, "src/payment.py", "def t(p, q):\n    return p * q\n", "feat: 합계")

    def tearDown(self):
        self.tmp.cleanup()

    def run_py(self, script, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                              cwd=cwd, env=dict(os.environ, **(env or {})))

    def files(self, title=TITLE, body=BODY):
        (self.dir / "t.txt").write_text(title, encoding="utf-8")
        (self.dir / "b.md").write_text(body, encoding="utf-8")
        (self.dir / "c.json").write_text(json.dumps(CLAIMS), encoding="utf-8")
        (self.dir / "v.json").write_text(json.dumps(VERIF), encoding="utf-8")
        return ["--title-file", str(self.dir / "t.txt"), "--body-file", str(self.dir / "b.md"),
                "--claims", str(self.dir / "c.json"), "--verification", str(self.dir / "v.json")]


class CollectContextTests(GitFixtureBase):
    def test_tells_the_author_the_key_will_be_added(self):
        r = self.run_py(COLLECT, "--repo-dir", str(self.work), "--no-gh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("이슈 키: PROJ-7 (브랜치 이름 맨 앞)", r.stdout)
        self.assertIn("제목에는 쓰지 마세요", r.stdout)

    def test_says_none_when_branch_does_not_start_with_a_key(self):
        S.git(self.work, "checkout", "-q", "-b", "feat/PROJ-7-x")
        r = self.run_py(COLLECT, "--repo-dir", str(self.work), "--no-gh")
        self.assertIn("이슈 키: 없음", r.stdout)


class ValidateTests(GitFixtureBase):
    def validate(self, *extra, **kw):
        return self.run_py(VALIDATE, *self.files(**kw), *extra, cwd=str(self.work))

    def test_current_branch_key_is_added_and_reported(self):
        r = self.validate()
        self.assertEqual(r.returncode, 0, r.stdout)
        out = json.loads(r.stdout)
        self.assertEqual((out["title"], out["issue_key"]), ("PROJ-7 " + TITLE, "PROJ-7"))

    def test_branch_without_key_keeps_the_title(self):
        S.git(self.work, "checkout", "-q", "-b", "feat/plain")
        out = json.loads(self.validate().stdout)
        self.assertEqual((out["title"], out["issue_key"]), (TITLE, None))

    def test_no_issue_key_flag(self):
        out = json.loads(self.validate("--no-issue-key").stdout)
        self.assertEqual((out["title"], out["issue_key"]), (TITLE, None))

    def test_branch_option_overrides_detection(self):
        out = json.loads(self.validate("--branch", "OTHER-9/x").stdout)
        self.assertEqual(out["title"], "OTHER-9 " + TITLE)

    def test_title_length_limit_counts_the_added_key(self):
        title = "가" * 66  # 66자는 통과하지만 'PROJ-7 '(7자)이 붙으면 73자
        r = self.validate(title=title)
        self.assertEqual(r.returncode, 1)
        self.assertIn("TITLE_TOO_LONG", r.stdout)
        self.assertEqual(self.validate("--no-issue-key", title=title).returncode, 0)

    def test_repository_title_pattern_applies_to_the_final_title(self):
        pat = r"^[A-Z]+-\d+ "
        self.assertEqual(self.validate("--title-pattern", pat).returncode, 0)
        self.assertEqual(self.validate("--title-pattern", pat, "--no-issue-key").returncode, 1)


class CreatePrTests(GitFixtureBase):
    def gh(self, existing=None):
        exe, env, log = S.make_fake_gh(self.dir, [
            {"match": ["repo view"], "stdout": json.dumps({"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}})},
            {"match": ["pr list"], "stdout": json.dumps(existing or [])},
            {"match": ["pr create"], "stdout": "https://github.com/o/r/pull/9\n"},
            {"match": ["pr edit"], "stdout": ""},
            {"match": ["pr view"], "stdout": json.dumps({"state": "OPEN", "isDraft": False, "baseRefName": "main",
                                                         "headRefName": "PROJ-7/feat/discount", "url": "u"})},
        ])
        self.log = log
        return env

    def create(self, *extra, existing=None, **kw):
        env = self.gh(existing)
        return self.run_py(CREATE, "--repo-dir", str(self.work), *self.files(**kw), *extra, env=env)

    def title_sent(self, verb):
        call = next(c["args"] for c in S.read_calls(self.log) if c["args"][:2] == ["pr", verb])
        return call[call.index("--title") + 1]

    def test_new_pr_gets_the_key_in_front(self):
        r = self.create("--post")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.title_sent("create"), "PROJ-7 " + TITLE)
        self.assertIn("이슈 키 PROJ-7는 브랜치 이름 맨 앞에서 가져옴", r.stdout)

    def test_updating_an_existing_pr_uses_the_same_title(self):
        existing = [{"number": 5, "url": "https://github.com/o/r/pull/5", "isDraft": False,
                     "body": "이전\n<!-- pr-write-squad -->"}]
        r = self.create("--post", existing=existing)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.title_sent("edit"), "PROJ-7 " + TITLE)

    def test_preview_shows_the_final_title_and_sends_nothing(self):
        r = self.create()
        self.assertIn("- 제목: PROJ-7 " + TITLE, r.stdout)
        self.assertEqual([c for c in S.read_calls(self.log) if c["args"][:2] in (["pr", "create"], ["pr", "edit"])], [])

    def test_key_is_not_doubled(self):
        self.create("--post", title="PROJ-7 " + TITLE)
        self.assertEqual(self.title_sent("create"), "PROJ-7 " + TITLE)

    def test_no_issue_key_flag(self):
        self.create("--post", "--no-issue-key")
        self.assertEqual(self.title_sent("create"), TITLE)

    def test_branch_without_a_key_is_untouched(self):
        S.git(self.work, "checkout", "-q", "-b", "feat/plain")
        self.create("--post")
        self.assertEqual(self.title_sent("create"), TITLE)

    def test_key_not_at_the_front_is_untouched(self):
        S.git(self.work, "checkout", "-q", "-b", "feat/PROJ-7-discount")
        self.create("--post")
        self.assertEqual(self.title_sent("create"), TITLE)

    def test_head_option_decides_the_key(self):
        S.git(self.work, "branch", "OTHER-3/topic")
        r = self.create("--head", "OTHER-3/topic", "--post")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.title_sent("create"), "OTHER-3 " + TITLE)

    def test_title_pattern_requires_the_key_form(self):
        r = self.create("--post", "--title-pattern", r"^[A-Z]+-\d+ ", "--no-issue-key")
        self.assertEqual(r.returncode, 1)
        self.assertIn("TITLE_PATTERN", r.stderr)


if __name__ == "__main__":
    unittest.main()
