"""post_review.py 테스트. 실제 GitHub에는 아무것도 보내지 않고, 호출을 기록하는 가짜 gh를 쓴다.

실행: python3 -m unittest discover -s tests
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "post_review.py"

spec = importlib.util.spec_from_file_location("post_review", SCRIPT)
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

FAKE_GH = """#!{py}
import json, os, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
args = sys.argv[1:]
stdin = sys.stdin.read() if "--input" in args else ""
with open(state["log"], "a") as fh:
    fh.write(json.dumps({{"args": args, "stdin": stdin}}, ensure_ascii=False) + "\\n")
key = " ".join(args)
for r in state["responses"]:
    if all(m in key for m in r["match"]):
        if r.get("fail"):
            sys.stderr.write(r["fail"]); sys.exit(1)
        sys.stdout.write(r["stdout"]); sys.exit(0)
sys.stderr.write("no fake response for: " + key); sys.exit(1)
"""

DIFF = """diff --git a/src/payment.py b/src/payment.py
index 111..222 100644
--- a/src/payment.py
+++ b/src/payment.py
@@ -10,3 +10,5 @@ def calc():
     a = 1
+    b = 2
+    c = 3
     d = 4
-    e = 5
+    e = 6
@@ -50,2 +52,3 @@
     x = 1
+    y = 2
     z = 3
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
diff --git a/notes.txt b/notes.txt
--- a/notes.txt
+++ b/notes.txt
@@ -1 +1,2 @@
 first
+second
\\ No newline at end of file
"""


def fnd(id, severity="C1", file="src/payment.py", line=12, verdict="CONFIRMED", **kw):
    f = dict(id=id, perspective="correctness", severity=severity, file=file, line=line,
             title=f"제목 {id}", evidence="x = 1", scenario="이런 상황이면 문제", suggestion="이렇게 고친다",
             verdict=verdict, verdict_reason="코드로 확인됨")
    if severity == "C2" and verdict == "CONFIRMED":
        f["verdict"] = "NOT_VERIFIED"
    f.update(kw)
    return f


class DiffParsingTests(unittest.TestCase):
    def setUp(self):
        self.c = pr.parse_commentable(DIFF)

    def test_added_and_context_lines_are_commentable(self):
        self.assertEqual(self.c["src/payment.py"], {10, 11, 12, 13, 14, 52, 53, 54})

    def test_removed_lines_are_not_commentable(self):
        self.assertNotIn(15, self.c["src/payment.py"])

    def test_deleted_file_is_not_commentable(self):
        self.assertNotIn("old.py", self.c)

    def test_no_newline_marker_does_not_shift_lines(self):
        self.assertEqual(self.c["notes.txt"], {1, 2})

    def test_empty_diff(self):
        self.assertEqual(pr.parse_commentable(""), {})

    def test_hunk_counts_stop_parsing_at_boundary(self):
        # '--- ' 로 시작하는 삭제 줄이 있어도 다음 파일 헤더로 오해하지 않는다
        diff = ("diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,1 @@\n"
                "--- removed line that looks like a header\n keep\n")
        self.assertEqual(pr.parse_commentable(diff)["a.txt"], {1})


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.commentable = pr.parse_commentable(DIFF)

    def plan(self, findings, existing=(), max_inline=20):
        return pr.make_plan(findings, self.commentable, set(existing), max_inline)

    def test_confirmed_in_diff_goes_inline(self):
        p = self.plan([fnd("A")])
        self.assertEqual([f["id"] for f in p["post"]], ["A"])

    def test_line_outside_diff_is_not_inline(self):
        p = self.plan([fnd("A", line=99), fnd("B", file="elsewhere.py", line=3)])
        self.assertEqual(p["post"], [])
        self.assertEqual({f["id"] for f in p["outside"]}, {"A", "B"})

    def test_only_must_fix_and_recommended_go_inline(self):
        p = self.plan([fnd("A", "C2"), fnd("B", verdict="REJECTED"), fnd("C", verdict="UNCERTAIN")])
        self.assertEqual(p["post"] + p["outside"], [])

    def test_must_fix_comes_first(self):
        p = self.plan([fnd("A", "C1", line=11), fnd("B", "C0", line=12)])
        self.assertEqual([f["id"] for f in p["post"]], ["B", "A"])

    def test_already_posted_marker_is_skipped(self):
        f = fnd("A")
        p = self.plan([f], existing={pr.inline_marker(f)})
        self.assertEqual(p["post"], [])
        self.assertEqual(len(p["already"]), 1)

    def test_marker_changes_when_title_changes(self):
        self.assertNotEqual(pr.inline_marker(fnd("A")), pr.inline_marker(fnd("A", title="다른 제목")))

    def test_max_inline_cap(self):
        fs = [fnd(f"F{i}", line=10 + i % 5) for i in range(5)]
        for i, f in enumerate(fs):
            f["title"] = f"제목 {i}"
        p = self.plan(fs, max_inline=2)
        self.assertEqual(len(p["post"]), 2)
        self.assertEqual(len(p["over_limit"]), 3)


class SecretScanTests(unittest.TestCase):
    def test_detects_common_secrets(self):
        samples = ["key AKIA" + "A" * 16, "token ghp_" + "a" * 36, "-----BEGIN RSA PRIVATE KEY-----",
                   'password = "hunter2hunter2"', "xoxb-1234567890-abcdef"]
        for s in samples:
            with self.subTest(sample=s[:12]):
                self.assertTrue(pr.scan_secrets([s]))

    def test_plain_code_is_fine(self):
        self.assertEqual(pr.scan_secrets(["total = price / (1 - discount)", "password = get_password()"]), [])

    def test_result_never_contains_the_secret(self):
        hits = pr.scan_secrets(["AKIA" + "B" * 16])
        self.assertTrue(all("AKIA" not in h for h in hits))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.fake = self.dir / "fake_gh"
        self.fake.write_text(FAKE_GH.format(py=sys.executable), encoding="utf-8")
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.log = self.dir / "calls.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, findings, *extra, pr_state="OPEN", mine_inline=(), mine_issue=(),
                review_fail=None, diff=DIFF, other_issue=()):
        def stream(items):
            return "\n".join(json.dumps(i, ensure_ascii=False) for i in items)
        responses = [
            {"match": ["--method DELETE"], "stdout": ""},
            {"match": ["pr", "view"], "stdout": json.dumps(
                {"state": pr_state, "headRefOid": "abcdef1234567", "url": "https://x/pull/7"})},
            {"match": ["pr", "diff"], "stdout": diff},
            {"match": ["api user "], "stdout": "me\n"},
            {"match": ["pulls/7/comments"], "stdout": stream(mine_inline)},
            {"match": ["issues/7/comments", "POST"], "stdout": json.dumps({"html_url": "https://x/c/1"})},
            {"match": ["issues/7/comments"], "stdout": stream(list(mine_issue) + list(other_issue))},
            {"match": ["issues/comments/", "PATCH"], "stdout": json.dumps({"html_url": "https://x/c/9"})},
        ]
        if review_fail:
            responses.append({"match": ["pulls/7/reviews"], "fail": review_fail})
        else:
            responses.append({"match": ["pulls/7/reviews"], "stdout": json.dumps({"html_url": "https://x/r/1"})})
        state = self.dir / "state.json"
        state.write_text(json.dumps({"log": str(self.log), "responses": responses}), encoding="utf-8")
        fpath = self.dir / "findings.json"
        lead = []
        if findings is not None:
            fpath.write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
            lead = [str(fpath)]
        env = dict(os.environ, PR_REVIEW_GH=str(self.fake), FAKE_GH_STATE=str(state))
        return subprocess.run([sys.executable, str(SCRIPT), *lead, "--pr", "7", "-R", "o/r", *extra],
                              capture_output=True, text=True, env=env)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines()]

    def writes(self):
        return [c for c in self.calls() if "--method" in c["args"]]

    def test_dry_run_is_default_and_writes_nothing(self):
        r = self.run_cli([fnd("A")])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.writes(), [])
        self.assertIn("미리보기", r.stdout)
        self.assertIn("--post", r.stdout)

    def test_post_sends_review_then_summary(self):
        r = self.run_cli([fnd("A", "C0", line=12), fnd("B", "C2", line=11)], "--post")
        self.assertEqual(r.returncode, 0, r.stderr)
        w = self.writes()
        self.assertEqual(len(w), 2)
        self.assertIn("pulls/7/reviews", " ".join(w[0]["args"]))
        self.assertIn("issues/7/comments", " ".join(w[1]["args"]))

    def test_review_is_comment_only_with_head_commit_and_right_side(self):
        self.run_cli([fnd("A", "C0", line=12)], "--post")
        payload = json.loads(self.writes()[0]["stdin"])
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], "abcdef1234567")
        self.assertEqual(payload["comments"][0]["path"], "src/payment.py")
        self.assertEqual(payload["comments"][0]["line"], 12)
        self.assertEqual(payload["comments"][0]["side"], "RIGHT")
        self.assertNotIn("APPROVE", json.dumps(payload))
        self.assertNotIn("REQUEST_CHANGES", json.dumps(payload))

    def test_inline_comment_text_has_no_internal_codes(self):
        self.run_cli([fnd("A", "C0", line=12)], "--post")
        body = json.loads(self.writes()[0]["stdin"])["comments"][0]["body"]
        self.assertIn("머지 전에 꼭 고쳐야 할 문제", body)
        self.assertIn("어떤 상황에서 문제가 되나요?", body)
        self.assertNotIn("C0", body)

    def test_summary_mentions_inline_and_outside_counts(self):
        self.run_cli([fnd("A", line=12), fnd("B", line=99)], "--post")
        summary = json.loads(self.writes()[1]["stdin"])["body"]
        self.assertTrue(summary.startswith(pr.SUMMARY_MARKER))
        self.assertIn("**1건**은 해당 코드 줄에도 댓글로 남겼습니다", summary)
        self.assertIn("**1건**", summary.split("이 댓글에만 있습니다")[0])

    def test_no_review_call_when_nothing_is_inline(self):
        self.run_cli([fnd("A", line=99), fnd("B", "C2")], "--post")
        w = self.writes()
        self.assertEqual(len(w), 1)
        self.assertIn("issues/7/comments", " ".join(w[0]["args"]))

    def test_summary_only_skips_diff_and_inline(self):
        r = self.run_cli([fnd("A", line=12)], "--post", "--summary-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.writes()), 1)
        self.assertFalse(any(c["args"][:2] == ["pr", "diff"] for c in self.calls()))

    def test_rerun_skips_already_posted_inline_and_updates_summary(self):
        f = fnd("A", line=12)
        r = self.run_cli([f], "--post",
                         mine_inline=[{"id": 1, "login": "me", "body": pr.inline_marker(f) + "\n이전 댓글"}],
                         mine_issue=[{"id": 55, "login": "me", "body": pr.SUMMARY_MARKER + "\n이전 요약"}])
        self.assertEqual(r.returncode, 0, r.stderr)
        w = self.writes()
        self.assertEqual(len(w), 1)
        self.assertIn("issues/comments/55", " ".join(w[0]["args"]))
        self.assertIn("PATCH", w[0]["args"])

    def test_other_users_comments_are_ignored(self):
        f = fnd("A", line=12)
        self.run_cli([f], "--post",
                     mine_inline=[{"id": 1, "login": "someone", "body": pr.inline_marker(f)}],
                     other_issue=[{"id": 55, "login": "someone", "body": pr.SUMMARY_MARKER}])
        w = self.writes()
        self.assertEqual(len(w), 2)  # 새 리뷰 + 새 요약 댓글
        self.assertIn("POST", w[1]["args"])

    def test_closed_pr_is_refused(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                self.log.unlink(missing_ok=True)
                r = self.run_cli([fnd("A")], "--post", pr_state=state)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(self.writes(), [])

    def test_secret_blocks_posting(self):
        r = self.run_cli([fnd("A", evidence="key = AKIA" + "Z" * 16)], "--post")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.writes(), [])
        self.assertNotIn("AKIAZZZ", r.stdout + r.stderr)

    def test_invalid_findings_block_posting(self):
        r = self.run_cli([fnd("A", scenario="")], "--post")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_oversize_summary_blocks_posting(self):
        fs = [fnd(f"M{i}", "C2", evidence="x" * 800, scenario="y" * 400, title=f"제목 {i}") for i in range(90)]
        r = self.run_cli(fs, "--post")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.writes(), [])

    def test_review_failure_stops_before_summary(self):
        r = self.run_cli([fnd("A", line=12)], "--post", review_fail="422 Unprocessable Entity")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(self.writes()), 1)
        self.assertIn("422", r.stderr)

    def test_bad_repo_format(self):
        fpath = self.dir / "f.json"
        fpath.write_text("[]", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), str(fpath), "--pr", "7", "-R", "not a repo"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    # ---- 철회(--retract) ----
    def retract_inputs(self):
        mine = pr.inline_marker(fnd("A"))
        inline = [
            {"id": 1, "login": "me", "body": mine + "\n내 스킬 댓글 1"},
            {"id": 2, "login": "me", "body": pr.inline_marker(fnd("B")) + "\n내 스킬 댓글 2"},
            {"id": 3, "login": "me", "body": "표식 없는 내가 직접 쓴 댓글"},
            {"id": 4, "login": "someone", "body": mine + "\n남이 쓴 표식 댓글"},
        ]
        issue = [
            {"id": 55, "login": "me", "body": pr.SUMMARY_MARKER + "\n내 요약"},
            {"id": 56, "login": "me", "body": "표식 없는 내 일반 댓글"},
            {"id": 57, "login": "someone", "body": pr.SUMMARY_MARKER + "\n남의 요약"},
        ]
        return inline, issue

    def deletes(self):
        return [" ".join(c["args"]) for c in self.calls() if "DELETE" in c["args"]]

    def test_retract_without_yes_only_previews(self):
        inline, issue = self.retract_inputs()
        r = self.run_cli(None, "--retract", mine_inline=inline, mine_issue=issue)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.deletes(), [])
        self.assertIn("인라인 댓글: 2건, 요약 댓글: 1건", r.stdout)
        self.assertIn("--yes", r.stdout)

    def test_retract_deletes_only_my_skill_comments(self):
        inline, issue = self.retract_inputs()
        r = self.run_cli(None, "--retract", "--yes", mine_inline=inline, mine_issue=issue)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.deletes()
        self.assertEqual(len(d), 3)
        self.assertTrue(any("pulls/comments/1 " in x + " " for x in d))
        self.assertTrue(any("pulls/comments/2 " in x + " " for x in d))
        self.assertTrue(any("issues/comments/55 " in x + " " for x in d))
        joined = " ".join(d)
        for forbidden in ("pulls/comments/3", "pulls/comments/4", "issues/comments/56", "issues/comments/57"):
            self.assertNotIn(forbidden, joined)

    def test_retract_works_on_closed_pr_and_needs_no_findings_file(self):
        inline, issue = self.retract_inputs()
        r = self.run_cli(None, "--retract", "--yes", pr_state="MERGED", mine_inline=inline, mine_issue=issue)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.deletes()), 3)

    def test_retract_with_nothing_to_delete(self):
        r = self.run_cli(None, "--retract", "--yes")
        self.assertEqual(r.returncode, 0)
        self.assertIn("지울 댓글이 없습니다", r.stdout)
        self.assertEqual(self.deletes(), [])

    def test_posting_without_findings_file_is_a_usage_error(self):
        r = self.run_cli(None, "--post")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.writes(), [])

    def test_dry_run_lists_what_would_happen(self):
        r = self.run_cli([fnd("A", line=12), fnd("B", line=99)])
        self.assertIn("인라인 댓글: 1건 작성", r.stdout)
        self.assertIn("diff에 없는 줄이라 요약에만 표시: 1건", r.stdout)
        self.assertIn("승인이나 변경 요청은 제출하지 않습니다", r.stdout)


if __name__ == "__main__":
    unittest.main()
