"""validate_findings.py 회귀 테스트. 실행: python3 -m unittest discover -s tests"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "validate_findings.py"

spec = importlib.util.spec_from_file_location("validate_findings", SCRIPT)
vf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vf)


def finding(**kw):
    base = dict(
        id="F1", perspective="correctness", severity="C1", file="a.py", line=3,
        title="t", evidence="x = 1", suggestion="s", verdict="CONFIRMED",
        verdict_reason="r", scenario="입력 → 잘못된 결과",
    )
    base.update(kw)
    return base


class DecisionTests(unittest.TestCase):
    def test_confirmed_c0_requests_changes(self):
        self.assertEqual(vf.decide([finding(severity="C0")]), "REQUEST_CHANGES")

    def test_confirmed_c1_only_comments(self):
        self.assertEqual(vf.decide([finding()]), "COMMENT")

    def test_only_c2_approves(self):
        f = finding(severity="C2", verdict="NOT_VERIFIED", scenario="")
        self.assertEqual(vf.decide([f]), "APPROVE")

    def test_empty_approves(self):
        self.assertEqual(vf.decide([]), "APPROVE")

    def test_rejected_c0_does_not_block(self):
        self.assertEqual(vf.decide([finding(severity="C0", verdict="REJECTED")]), "APPROVE")

    def test_uncertain_c0_does_not_block(self):
        self.assertEqual(vf.decide([finding(severity="C0", verdict="UNCERTAIN")]), "APPROVE")

    def test_many_c2_do_not_offset_c0(self):
        fs = [finding(severity="C0")] + [
            finding(id=f"M{i}", severity="C2", verdict="NOT_VERIFIED", scenario="")
            for i in range(20)
        ]
        self.assertEqual(vf.decide(fs), "REQUEST_CHANGES")


class ScoreTests(unittest.TestCase):
    def test_scores_follow_rubric_table(self):
        c0 = finding(severity="C0")
        two_c1 = [finding(id="A"), finding(id="B")]
        self.assertEqual(vf.score([c0], "correctness"), 4)
        self.assertEqual(vf.score(two_c1, "correctness"), 6)
        self.assertEqual(vf.score([finding()], "correctness"), 8)
        self.assertEqual(vf.score([], "correctness"), 10)

    def test_other_perspective_unaffected(self):
        self.assertEqual(vf.score([finding(severity="C0")], "security"), 10)

    def test_rejected_does_not_lower_score(self):
        self.assertEqual(vf.score([finding(severity="C0", verdict="REJECTED")], "correctness"), 10)


class CheckTests(unittest.TestCase):
    def test_valid_has_no_errors(self):
        self.assertEqual(vf.check([finding()]), [])

    def test_c1_requires_scenario(self):
        self.assertTrue(vf.check([finding(scenario="")]))

    def test_every_severity_requires_scenario(self):
        for sev, verdict in (("C0", "CONFIRMED"), ("C1", "CONFIRMED"), ("C2", "NOT_VERIFIED")):
            with self.subTest(severity=sev):
                f = finding(severity=sev, verdict=verdict, scenario="")
                self.assertTrue(any("scenario" in e for e in vf.check([f])))

    def test_c1_cannot_skip_verification(self):
        self.assertTrue(vf.check([finding(verdict="NOT_VERIFIED")]))

    def test_duplicate_id(self):
        self.assertTrue(any("중복" in e for e in vf.check([finding(), finding()])))

    def test_bad_line_and_enum(self):
        errs = vf.check([finding(line=0, severity="C9", perspective="style")])
        self.assertGreaterEqual(len(errs), 3)

    def test_bool_is_not_a_line_number(self):
        self.assertTrue(vf.check([finding(line=True)]))

    def test_empty_evidence(self):
        self.assertTrue(vf.check([finding(evidence="")]))

    def test_also_reported_by_accepts_other_perspectives(self):
        self.assertEqual(vf.check([finding(also_reported_by=["testing", "security"])]), [])

    def test_also_reported_by_rejects_own_unknown_or_non_list(self):
        for bad in (["correctness"], ["style"], "testing", [1]):
            with self.subTest(value=bad):
                self.assertTrue(any("also_reported_by" in e
                                    for e in vf.check([finding(also_reported_by=bad)])))


class TraceRuleTests(unittest.TestCase):
    """테스트 부재 지적의 CONFIRMED는 호출 경로 추적(trace)이 있어야 한다."""

    def make_testing_c1(self, **kw):
        return finding(perspective="testing", severity="C1", verdict="CONFIRMED", **kw)

    def test_confirmed_testing_c1_without_trace_is_error(self):
        errs = vf.check([self.make_testing_c1()])
        self.assertTrue(any("trace" in e for e in errs))

    def test_confirmed_testing_c1_with_trace_ok(self):
        self.assertEqual(vf.check([self.make_testing_c1(trace=["t.py:3 → a.py:9 → a.py:20"])]), [])

    def test_empty_or_blank_trace_is_error(self):
        for bad in ([], [""], ["  "], "t.py:3 → a.py:9", None, [1]):
            with self.subTest(trace=bad):
                self.assertTrue(vf.check([self.make_testing_c1(trace=bad)]))

    def test_testing_c0_confirmed_also_requires_trace(self):
        f = finding(perspective="testing", severity="C0", verdict="CONFIRMED")
        self.assertTrue(any("trace" in e for e in vf.check([f])))

    def test_rejected_or_uncertain_testing_finding_needs_no_trace(self):
        for v in ("REJECTED", "UNCERTAIN"):
            with self.subTest(verdict=v):
                f = finding(perspective="testing", severity="C1", verdict=v)
                self.assertEqual(vf.check([f]), [])

    def test_testing_c2_needs_no_trace(self):
        f = finding(perspective="testing", severity="C2", verdict="NOT_VERIFIED")
        self.assertEqual(vf.check([f]), [])

    def test_other_perspectives_need_no_trace(self):
        for p in ("correctness", "security", "maintainability"):
            with self.subTest(perspective=p):
                self.assertEqual(vf.check([finding(perspective=p)]), [])


class SourceCheckTests(unittest.TestCase):
    """--root 대조: 환각된 줄 번호나 존재하지 않는 인용을 경고로 잡는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        lines = [f"line_{i} = {i}" for i in range(1, 61)]
        lines[35] = 'return datetime.strptime(text[:10], "%Y-%m-%d").date()'
        (self.root / "src").mkdir()
        (self.root / "src" / "mod.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def warn(self, **kw):
        base = dict(file="src/mod.py", line=36,
                    evidence='return datetime.strptime(text[:10], "%Y-%m-%d").date()')
        base.update(kw)
        return vf.check_sources([finding(**base)], self.root)

    def test_correct_line_has_no_warning(self):
        self.assertEqual(self.warn(), [])

    def test_small_offset_is_tolerated(self):
        self.assertEqual(self.warn(line=40), [])

    def test_wrong_line_points_to_real_line(self):
        w = self.warn(line=50)
        self.assertEqual(len(w), 1)
        self.assertIn("src/mod.py:36", w[0])

    def test_line_beyond_file_length(self):
        w = self.warn(line=396)
        self.assertEqual(len(w), 1)
        self.assertIn("60줄", w[0])

    def test_evidence_not_in_file(self):
        w = self.warn(evidence="this code does not exist anywhere")
        self.assertEqual(len(w), 1)
        self.assertIn("찾지 못함", w[0])

    def test_missing_file(self):
        w = self.warn(file="src/nope.py")
        self.assertEqual(len(w), 1)
        self.assertIn("읽을 수 없음", w[0])

    def test_path_traversal_is_not_followed(self):
        w = self.warn(file="../../etc/passwd")
        self.assertEqual(len(w), 1)
        self.assertIn("저장소 밖", w[0])

    def test_rejected_findings_are_skipped(self):
        f = finding(file="src/nope.py", line=999, verdict="REJECTED")
        self.assertEqual(vf.check_sources([f], self.root), [])

    def test_joined_evidence_matches_any_segment(self):
        w = self.warn(evidence='nonsense that is absent ... return datetime.strptime(text[:10], "%Y-%m-%d").date()')
        self.assertEqual(w, [])

    def test_whitespace_differences_are_ignored(self):
        w = self.warn(evidence='return   datetime.strptime(text[:10],   "%Y-%m-%d").date()')
        self.assertEqual(w, [])

    def test_very_short_evidence_is_not_compared(self):
        self.assertEqual(self.warn(evidence="x = 1"), [])


class CliTests(unittest.TestCase):
    def run_cli(self, payload):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.json"
            p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
            return subprocess.run([sys.executable, str(SCRIPT), str(p)],
                                  capture_output=True, text=True)

    def test_c2_is_counted_even_though_not_verified(self):
        """회귀: C2(NOT_VERIFIED)가 counts에서 0으로 나오던 버그."""
        fs = [finding(id=f"M{i}", severity="C2", verdict="NOT_VERIFIED")
              for i in range(3)]
        out = json.loads(self.run_cli(fs).stdout)
        self.assertEqual(out["counts"]["C2"], 3)
        self.assertEqual(out["verdict"], "APPROVE")

    def test_rejected_c2_not_counted(self):
        fs = [finding(severity="C2", verdict="REJECTED")]
        out = json.loads(self.run_cli(fs).stdout)
        self.assertEqual(out["counts"]["C2"], 0)
        self.assertEqual(out["rejected"], ["F1"])

    def test_uncertain_listed(self):
        out = json.loads(self.run_cli([finding(verdict="UNCERTAIN")]).stdout)
        self.assertEqual(out["uncertain"], ["F1"])
        self.assertEqual(out["verdict"], "APPROVE")

    def test_root_option_adds_warnings_without_changing_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            fs = [finding(severity="C0", file="a.py", line=999)]
            p = Path(d) / "f.json"
            p.write_text(json.dumps(fs), encoding="utf-8")
            plain = subprocess.run([sys.executable, str(SCRIPT), str(p)],
                                   capture_output=True, text=True)
            with_root = subprocess.run([sys.executable, str(SCRIPT), str(p), "--root", d],
                                       capture_output=True, text=True)
        self.assertEqual(with_root.returncode, 0)
        a, b = json.loads(plain.stdout), json.loads(with_root.stdout)
        self.assertNotIn("warnings", a)
        self.assertEqual(a["verdict"], b["verdict"])
        self.assertEqual(len(b["warnings"]), 1)

    def test_root_option_without_value_is_usage_error(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "x.json", "--root"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_exit_codes(self):
        self.assertEqual(self.run_cli([finding()]).returncode, 0)
        self.assertEqual(self.run_cli([finding(scenario="")]).returncode, 1)
        self.assertEqual(self.run_cli("{not json").returncode, 2)
        self.assertEqual(self.run_cli({"a": 1}).returncode, 2)

    def test_sample_file_decision(self):
        sample = ROOT / "examples" / "sample-findings.json"
        r = subprocess.run([sys.executable, str(SCRIPT), str(sample)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "REQUEST_CHANGES")
        self.assertEqual(out["counts"], {"C0": 1, "C1": 1, "C2": 1})


if __name__ == "__main__":
    unittest.main()
