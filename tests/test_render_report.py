"""render_report.py 테스트. 실행: python3 -m unittest discover -s tests"""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "render_report.py"

spec = importlib.util.spec_from_file_location("render_report", SCRIPT)
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)


def finding(**kw):
    base = dict(
        id="F1", perspective="correctness", severity="C1", file="a.py", line=3,
        title="제목", evidence="x = 1", suggestion="이렇게 고친다", verdict="CONFIRMED",
        verdict_reason="코드로 확인됨", scenario="이런 입력이면 이런 문제가 생긴다",
    )
    base.update(kw)
    return base


def c2(**kw):
    return finding(severity="C2", verdict="NOT_VERIFIED",
                   verdict_reason="C2는 검증 대상이 아니다", **kw)


class RenderTests(unittest.TestCase):
    def test_headline_follows_decision(self):
        cases = [
            ([finding(severity="C0")], "⛔ 머지 전에 수정이 필요합니다"),
            ([finding()], "💬 수정을 권장하는 사항이 있습니다"),
            ([c2()], "✅ 확인된 문제가 없습니다"),
            ([], "✅ 확인된 문제가 없습니다"),
        ]
        for fs, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, rr.render(fs, []).splitlines()[0])

    def test_internal_level_codes_are_not_shown_to_users(self):
        fs = [finding(severity="C0", id="A"), finding(id="B"), c2(id="C")]
        text = rr.render(fs, [])
        for code in ("C0", "C1", "C2"):
            self.assertNotRegex(text, rf"\b{code}\b")

    def test_every_finding_is_expanded_with_scenario_and_fix(self):
        fs = [finding(id="A", title="첫째", scenario="상황A", suggestion="고침A"),
              c2(id="B", title="둘째", scenario="상황B", suggestion="고침B")]
        text = rr.render(fs, [])
        for needle in ("첫째", "상황A", "고침A", "둘째", "상황B", "고침B"):
            self.assertIn(needle, text)

    def test_verification_line_per_finding(self):
        text = rr.render([finding(id="A"), c2(id="B")], [])
        self.assertIn("✅ **확인됨**", text)
        self.assertIn("별도 검증은 하지 않았습니다", text)

    def test_rejected_findings_are_not_shown_but_counted(self):
        text = rr.render([finding(id="A", title="반려될 제목", verdict="REJECTED")], [])
        self.assertNotIn("반려될 제목", text)
        self.assertIn("반려된 지적 1건", text)

    def test_uncertain_goes_to_its_own_section_not_the_counts(self):
        text = rr.render([finding(id="A", title="애매한 지적", verdict="UNCERTAIN",
                                  verdict_reason="파일을 더 봐야 함")], [])
        self.assertIn("확인이 필요한 항목 (1건)", text)
        self.assertIn("처음 판단한 등급: 고치는 것을 권장", text)
        self.assertIn("고치는 것을 권장 (0건)", text)
        self.assertIn("✅ 확인된 문제가 없습니다", text.splitlines()[0])

    def test_empty_sections_say_none(self):
        text = rr.render([], [])
        self.assertEqual(text.count("\n없음\n"), 4)
        self.assertIn("별도로 기록된 항목이 없습니다", text)

    def test_not_reviewed_items_are_listed(self):
        text = rr.render([], ["테스트는 실행하지 못했습니다", "diff 밖 파일은 보지 않았습니다"])
        self.assertIn("- 테스트는 실행하지 못했습니다", text)
        self.assertIn("- diff 밖 파일은 보지 않았습니다", text)

    def test_trace_is_collapsible(self):
        f = finding(perspective="testing", trace=["t.py:3 → a.py:9"])
        text = rr.render([f], [])
        self.assertIn("<details><summary>검증자가 따라간 경로</summary>", text)
        self.assertIn("- t.py:3 → a.py:9", text)

    def test_evidence_with_backticks_does_not_break_the_fence(self):
        f = finding(evidence="print(```x```)")
        text = rr.render([f], [])
        self.assertIn("````\nprint(```x```)\n````", text)

    def test_score_table_has_all_perspectives(self):
        text = rr.render([finding(perspective="testing", severity="C0")], [])
        for name in ("정확성", "보안", "가독성·유지보수", "테스트"):
            self.assertRegex(text, rf"\| {name} \| \d+/10 \|")
        self.assertRegex(text, r"\| 테스트 \| 4/10 \| 꼭 고쳐야 함 1 \|")

    def test_note_is_placed_after_the_headline_not_before_it(self):
        text = rr.render([finding()], [], note="> 안내 문장입니다.")
        self.assertTrue(text.startswith("## 🔍 PR 리뷰 결과"))
        self.assertLess(text.index("## 🔍"), text.index("> 안내 문장입니다."))
        self.assertLess(text.index("> 안내 문장입니다."), text.index("| 관점 |"))

    def test_also_reported_by_is_shown_without_touching_the_title(self):
        f = finding(title="깨끗한 제목", also_reported_by=["maintainability", "testing"])
        text = rr.render([f], [])
        self.assertIn("#### 1. 깨끗한 제목\n", text)
        self.assertIn("같은 문제를 가독성·유지보수, 테스트 관점의 리뷰어도 지적했습니다.", text)

    def test_no_also_reported_line_when_absent(self):
        self.assertNotIn("관점의 리뷰어도 지적했습니다", rr.render([finding()], []))

    def test_numbering_restarts_per_section(self):
        text = rr.render([finding(id="A"), finding(id="B"), c2(id="C")], [])
        self.assertIn("#### 1.", text)
        self.assertIn("#### 2.", text)
        self.assertEqual(text.count("#### 1."), 2)


class CliTests(unittest.TestCase):
    def run_cli(self, payload, *extra):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.json"
            p.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                         encoding="utf-8")
            return subprocess.run([sys.executable, str(SCRIPT), str(p), *extra],
                                  capture_output=True, text=True)

    def test_sample_file_renders(self):
        r = subprocess.run([sys.executable, str(SCRIPT), str(ROOT / "examples" / "sample-findings.json")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("⛔ 머지 전에 수정이 필요합니다", r.stdout)
        self.assertIn("검증에서 반려된 지적 1건", r.stdout)

    def test_invalid_findings_produce_no_report(self):
        r = self.run_cli([finding(scenario="")])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("scenario", r.stderr)

    def test_bad_input_exit_code(self):
        self.assertEqual(self.run_cli("{not json").returncode, 2)
        self.assertEqual(self.run_cli({"a": 1}).returncode, 2)

    def test_not_reviewed_option(self):
        r = self.run_cli([], "--not-reviewed", "첫째", "--not-reviewed", "둘째")
        self.assertEqual(r.returncode, 0)
        self.assertIn("- 첫째", r.stdout)
        self.assertIn("- 둘째", r.stdout)

    def test_size_warning(self):
        fs = [c2(id=f"M{i}", evidence="x" * 500, scenario="y" * 300) for i in range(80)]
        r = self.run_cli(fs)
        self.assertEqual(r.returncode, 0)
        self.assertIn("GitHub 댓글 한도", r.stderr)


if __name__ == "__main__":
    unittest.main()
