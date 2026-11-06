"""collect_context.py 테스트(실제 임시 git 저장소 사용)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support as S

SCRIPT = S.SCRIPTS / "collect_context.py"


class CollectContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work, self.remote = S.make_repo(self.tmp.name)
        S.git(self.work, "checkout", "-q", "-b", "feat/PROJ-12-discount")
        S.commit_file(self.work, "src/payment.py", "def total(p, q):\n    return p * q\n", "feat: 합계 계산 추가 (#34)")
        S.commit_file(self.work, "src/discount.py", "RATE = 0.1\n", "feat: 할인율 상수")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *extra, env=None):
        e = dict(os.environ, **(env or {}))
        return subprocess.run([sys.executable, str(SCRIPT), "--repo-dir", str(self.work), "--no-gh", *extra],
                              capture_output=True, text=True, encoding="utf-8", env=e)

    def test_sections_and_basic_facts(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        for section in ("## branch", "## commits", "## changed files", "## stat", "## pr template",
                        "## contributing", "## recent merged PR titles", "## issue hints", "## diff"):
            self.assertIn(section, r.stdout)
        self.assertIn("head: feat/PROJ-12-discount", r.stdout)
        self.assertIn("base: main", r.stdout)
        self.assertIn("앞선 커밋: 2개", r.stdout)
        self.assertIn("A\tsrc/payment.py", r.stdout)
        self.assertIn("+    return p * q", r.stdout)

    def test_diff_is_the_branch_only_not_base(self):
        self.assertNotIn("# demo", self.run_cli().stdout.split("## diff")[1])

    def test_issue_hints_from_branch_and_commits(self):
        out = self.run_cli().stdout.split("## issue hints")[1].split("## diff")[0]
        self.assertIn("PROJ-12", out)
        self.assertIn("#34", out)

    def test_no_template_message(self):
        self.assertIn("없음 (references/conventions-template.md", self.run_cli().stdout)

    def test_template_is_included(self):
        S.commit_file(self.work, ".github/pull_request_template.md", "## 개요\n<!-- 설명 -->\n", "chore: 템플릿")
        out = self.run_cli().stdout
        self.assertIn("경로: .github/pull_request_template.md", out)
        self.assertIn("## 개요", out.split("## pr template")[1])

    def test_multiple_templates_are_noted(self):
        S.commit_file(self.work, ".github/PULL_REQUEST_TEMPLATE/a.md", "## A\n", "chore: a")
        S.commit_file(self.work, ".github/PULL_REQUEST_TEMPLATE/b.md", "## B\n", "chore: b")
        out = self.run_cli().stdout.split("## pr template")[1]
        self.assertIn("템플릿이 2개", out)

    def test_contributing_excerpt_keeps_only_relevant_lines(self):
        S.commit_file(self.work, "CONTRIBUTING.md",
                      "# 기여\n날씨 이야기\nPR 제목은 type(scope): 요약 형식을 따릅니다.\n점심 메뉴\n", "docs: 기여 안내")
        out = self.run_cli().stdout.split("## contributing")[1].split("## recent")[0]
        self.assertIn("PR 제목은 type(scope)", out)
        self.assertNotIn("점심 메뉴", out)

    def test_uncommitted_changes_are_reported(self):
        (self.work / "wip.txt").write_text("x", encoding="utf-8")
        self.assertIn("커밋되지 않은 변경: 1개 파일", self.run_cli().stdout)

    def test_clean_tree_is_reported(self):
        self.assertIn("커밋되지 않은 변경: 없음", self.run_cli().stdout)

    def test_on_base_branch_exits_one(self):
        S.git(self.work, "checkout", "-q", "main")
        r = self.run_cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("베이스(main)와 같습니다", r.stderr)

    def test_no_commits_ahead_exits_one(self):
        S.git(self.work, "checkout", "-q", "-b", "empty-branch", "main")
        r = self.run_cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("앞선 커밋이 없어", r.stderr)

    def test_detached_head_is_an_error(self):
        S.git(self.work, "checkout", "-q", "--detach")
        self.assertEqual(self.run_cli().returncode, 2)

    def test_explicit_base_option(self):
        S.git(self.work, "checkout", "-q", "-b", "base2", "feat/PROJ-12-discount~1")
        S.commit_file(self.work, "later.txt", "z\n", "feat: 추가")
        r = self.run_cli("--base", "feat/PROJ-12-discount")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("앞선 커밋: 1개", r.stdout)

    def test_unknown_base_is_an_error(self):
        self.assertEqual(self.run_cli("--base", "nope").returncode, 2)

    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(SCRIPT), "--repo-dir", d, "--no-gh"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)

    def test_recent_pr_titles_come_from_gh(self):
        exe, env, log = S.make_fake_gh(self.tmp.name, [
            {"match": ["pr list"], "stdout": "#7 feat(a): 이전 PR 제목\n#6 fix(b): 다른 제목"}])
        r = subprocess.run([sys.executable, str(SCRIPT), "--repo-dir", str(self.work), "-R", "o/r"],
                           capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, **env))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("#7 feat(a): 이전 PR 제목", r.stdout)
        call = S.read_calls(log)[0]["args"]
        self.assertEqual(call[:3], ["pr", "list", "--state"])
        self.assertIn("o/r", call)

    def test_no_merged_prs_is_not_reported_as_a_failure(self):
        exe, env, log = S.make_fake_gh(self.tmp.name, [{"match": ["pr list"], "stdout": ""}])
        r = subprocess.run([sys.executable, str(SCRIPT), "--repo-dir", str(self.work)],
                           capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, **env))
        self.assertEqual(r.returncode, 0)
        self.assertIn("머지된 PR이 아직 없음", r.stdout)
        self.assertNotIn("가져오지 못함", r.stdout)

    def test_gh_failure_does_not_break_collection(self):
        exe, env, log = S.make_fake_gh(self.tmp.name, [{"match": ["pr list"], "fail": "network down"}])
        r = subprocess.run([sys.executable, str(SCRIPT), "--repo-dir", str(self.work)],
                           capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, **env))
        self.assertEqual(r.returncode, 0)
        self.assertIn("가져오지 못함: network down", r.stdout)

    def test_script_is_read_only(self):
        before = S.git(self.work, "status", "--porcelain=v1"), S.git(self.work, "rev-parse", "HEAD")
        self.run_cli()
        self.assertEqual(before, (S.git(self.work, "status", "--porcelain=v1"), S.git(self.work, "rev-parse", "HEAD")))


class FindGitTests(unittest.TestCase):
    def test_returns_an_absolute_path_so_shell_wrappers_use_the_same_git(self):
        import _common as C
        old = C._GIT
        try:
            C._GIT = None
            path = C.find_git()
            self.assertTrue(os.path.isabs(path), path)
            self.assertTrue(os.access(path, os.X_OK))
        finally:
            C._GIT = old

    def test_skips_a_git_that_cannot_run(self):
        import _common as C
        old_env, old_cache = os.environ.get("PR_WRITE_GIT"), C._GIT
        try:
            C._GIT = None
            os.environ["PR_WRITE_GIT"] = "/nonexistent/broken-git"
            self.assertNotEqual(C.find_git(), "/nonexistent/broken-git")
        finally:
            C._GIT = old_cache
            if old_env is None:
                os.environ.pop("PR_WRITE_GIT", None)
            else:
                os.environ["PR_WRITE_GIT"] = old_env


if __name__ == "__main__":
    unittest.main()
