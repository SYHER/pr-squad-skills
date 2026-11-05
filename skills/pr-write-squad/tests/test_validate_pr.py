"""validate_pr.py 테스트. 실행: python3 -m unittest discover -s tests"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (scripts 경로 추가)
import validate_pr as V

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_pr.py"

TITLE = "feat(payment): 할인율 적용 추가"
BODY = """## 개요
할인율(discount)을 받아 합계에 적용합니다.

## 변경 이유
결제 금액에 할인을 적용해야 했습니다.

## 이번에 하지 않은 것
- 쿠폰 코드는 다루지 않습니다.

## 확인 방법
- 단위 테스트를 실행했고 통과했습니다.
"""
TEMPLATE = """## 개요
<!-- 설명 -->

## 변경 이유
<!-- 이유 -->

## 이번에 하지 않은 것
<!-- 범위 -->

## 확인 방법
<!-- 검증 -->

## 먼저 봐 주세요 (선택)
"""
CLAIMS = [{"id": "K1", "kind": "behavior", "text": "discount를 합계에 적용한다",
           "evidence": "src/payment.py:12", "verdict": "SUPPORTED", "note": ""}]
VERIF = [{"what": "단위 테스트", "command": "python3 -m unittest", "result": "passed",
          "evidence": "Ran 2 tests OK", "reason": ""}]


def codes(errors):
    return [e["code"] for e in errors]


def check(title=TITLE, body=BODY, template=None, claims=CLAIMS, verification=VERIF, **kw):
    return V.validate(title, body, template, claims, verification, **kw)


class TitleTests(unittest.TestCase):
    def test_valid_baseline_has_no_errors(self):
        errors, _ = check()
        self.assertEqual(errors, [])

    def test_empty_title(self):
        self.assertIn("TITLE_EMPTY", codes(check(title="  ")[0]))

    def test_too_long(self):
        self.assertIn("TITLE_TOO_LONG", codes(check(title="가" * 73)[0]))
        self.assertNotIn("TITLE_TOO_LONG", codes(check(title="가" * 72)[0]))

    def test_trailing_period(self):
        self.assertIn("TITLE_PERIOD", codes(check(title="할인 적용을 추가한다.")[0]))

    def test_multiline(self):
        self.assertIn("TITLE_MULTILINE", codes(check(title="첫 줄\n둘째 줄")[0]))

    def test_placeholder(self):
        for t in ("feat: <제목>", "feat: TODO 나중에", "feat: {{summary}}"):
            with self.subTest(title=t):
                self.assertIn("TITLE_PLACEHOLDER", codes(check(title=t)[0]))

    def test_repository_title_pattern(self):
        pat = r"^(feat|fix|docs)(\([\w-]+\))?: .+"
        self.assertEqual(check(title="feat(a): 좋은 제목", title_pattern=pat)[0], [])
        self.assertIn("TITLE_PATTERN", codes(check(title="그냥 제목", title_pattern=pat)[0]))


class BodyTests(unittest.TestCase):
    def test_empty_body(self):
        self.assertIn("BODY_EMPTY", codes(check(body="\n  \n")[0]))

    def test_too_long(self):
        self.assertIn("BODY_TOO_LONG", codes(check(body=BODY + "x" * 70000)[0]))

    def test_leftover_template_comment(self):
        self.assertIn("PLACEHOLDER", codes(check(body=BODY + "\n<!-- 설명을 적어 주세요 -->\n")[0]))

    def test_own_marker_comment_is_allowed(self):
        self.assertEqual(check(body=BODY + "\n" + V.MARKER + "\n")[0], [])

    def test_placeholder_words(self):
        for extra in ("TODO: 채우기", "TBD", "FIXME", "{{ticket}}", "여기에 작성하세요", "(이유 미확인: 확인 필요)"):
            with self.subTest(extra=extra):
                self.assertIn("PLACEHOLDER", codes(check(body=BODY + "\n- " + extra + "\n")[0]))

    def test_placeholder_inside_html_comment_counts_once_as_comment(self):
        errors, _ = check(body=BODY + "\n<!-- TODO -->\n")
        self.assertIn("PLACEHOLDER", codes(errors))


class TemplateTests(unittest.TestCase):
    def test_body_follows_template(self):
        errors, _ = check(body=BODY, template=TEMPLATE)
        self.assertEqual(errors, [])

    def test_missing_section(self):
        body = BODY.replace("## 이번에 하지 않은 것\n- 쿠폰 코드는 다루지 않습니다.\n\n", "")
        errors, _ = check(body=body, template=TEMPLATE)
        self.assertTrue(any(e["code"] == "TEMPLATE_SECTION" and "이번에 하지 않은 것" in e["message"] for e in errors))

    def test_empty_section(self):
        body = BODY.replace("- 쿠폰 코드는 다루지 않습니다.", "-")
        errors, _ = check(body=body, template=TEMPLATE)
        self.assertTrue(any(e["code"] == "TEMPLATE_SECTION" and "내용이 비어" in e["message"] for e in errors))

    def test_optional_section_may_be_missing(self):
        errors, _ = check(body=BODY, template=TEMPLATE)
        self.assertFalse(any("먼저 봐" in e["message"] for e in errors))

    def test_headings_inside_code_fence_are_ignored(self):
        body = BODY + "\n```\n## 먼저 봐 주세요\n```\n"
        self.assertEqual(V.parse_sections(body)[-1][1], "확인 방법")

    def test_subsections_belong_to_parent(self):
        body = "## 확인 방법\n### 자동\n- 테스트\n"
        sections = V.parse_sections(body)
        self.assertIn("테스트", sections[0][2])

    def test_english_template(self):
        tpl = "## Summary\n\n## Verification\n"
        body = "## Summary\nAdds discount.\n\n## Verification\nRan unit tests, they passed.\n"
        self.assertEqual(check(body=body, template=tpl)[0], [])


class ClaimTests(unittest.TestCase):
    def claim(self, **kw):
        c = dict(CLAIMS[0]); c.update(kw)
        return [c]

    def test_supported_with_evidence_ok(self):
        self.assertEqual(check(claims=self.claim())[0], [])

    def test_unsupported_blocks(self):
        errors, _ = check(claims=self.claim(verdict="UNSUPPORTED", note="코드와 다름"))
        self.assertIn("CLAIM_NOT_SUPPORTED", codes(errors))

    def test_uncertain_blocks(self):
        self.assertIn("CLAIM_NOT_SUPPORTED", codes(check(claims=self.claim(verdict="UNCERTAIN", note="더 봐야 함"))[0]))

    def test_unchecked_blocks(self):
        self.assertIn("CLAIM_NOT_SUPPORTED", codes(check(claims=self.claim(verdict=""))[0]))

    def test_supported_needs_file_line_evidence(self):
        for ev in ("", "src/payment.py", "payment 함수", "src/payment.py:abc"):
            with self.subTest(evidence=ev):
                self.assertIn("CLAIM_NOT_SUPPORTED", codes(check(claims=self.claim(evidence=ev))[0]))
        self.assertEqual(check(claims=self.claim(evidence="src/a.py:10-20"))[0], [])

    def test_multiple_evidence_locations_are_accepted(self):
        for ev in ("src/a.py:4, tests/test_a.py:15", "src/a.py:4 (정의), tests/a.py:4 (import), tests/a.py:15-21",
                   "src/a.py:1-7; tests/a.py:3"):
            with self.subTest(evidence=ev):
                self.assertEqual(check(claims=self.claim(evidence=ev))[0], [])

    def test_one_bad_item_among_many_is_rejected(self):
        for ev in ("src/a.py:4, 그냥 설명", ",", "src/a.py:4 tests/a.py:5", "src/a.py:four, tests/a.py:5",
                   "git diff --name-status origin/main...HEAD"):
            with self.subTest(evidence=ev):
                self.assertIn("CLAIM_NOT_SUPPORTED", codes(check(claims=self.claim(evidence=ev))[0]))

    def test_a_stray_trailing_comma_does_not_hide_a_valid_location(self):
        self.assertEqual(check(claims=self.claim(evidence="src/a.py:4,"))[0], [])

    def test_bad_kind_and_duplicate_id(self):
        errors, _ = check(claims=self.claim(kind="opinion") + self.claim())
        self.assertIn("CLAIMS_FORMAT", codes(errors))
        errors, _ = check(claims=self.claim() + self.claim())
        self.assertIn("CLAIMS_FORMAT", codes(errors))

    def test_missing_claims_only_warns(self):
        errors, warnings = check(claims=None)
        self.assertEqual(errors, [])
        self.assertIn("NO_CLAIMS", codes(warnings))


class VerificationTests(unittest.TestCase):
    def test_passed_needs_command_and_evidence(self):
        for field in ("command", "evidence"):
            with self.subTest(missing=field):
                v = [dict(VERIF[0], **{field: ""})]
                self.assertIn("VERIFICATION_EVIDENCE", codes(check(verification=v)[0]))

    def test_not_run_needs_reason(self):
        v = [{"what": "린터", "command": "ruff", "result": "not_run", "evidence": "", "reason": ""}]
        self.assertIn("VERIFICATION_EVIDENCE", codes(check(verification=v)[0]))

    def test_bad_result_value(self):
        self.assertIn("VERIFICATION_FORMAT", codes(check(verification=[{"what": "x", "result": "ok"}])[0]))

    def test_pass_claim_without_passed_record_is_blocked(self):
        errors, _ = check(verification=[])
        self.assertIn("UNBACKED_PASS_CLAIM", codes(errors))

    def test_pass_claim_with_only_not_run_is_blocked(self):
        v = [{"what": "테스트", "command": "t", "result": "not_run", "evidence": "", "reason": "환경 없음"}]
        body = BODY + "\n- 테스트는 실행하지 못했습니다(환경 없음).\n"
        errors, _ = check(body=body, verification=v)
        self.assertIn("UNBACKED_PASS_CLAIM", codes(errors))  # 기본 BODY의 '통과했습니다' 때문

    def test_negated_or_honest_sentences_are_not_pass_claims(self):
        body = BODY.replace("- 단위 테스트를 실행했고 통과했습니다.",
                            "- 테스트는 실행하지 못했습니다(환경 없음). 통과 여부는 알 수 없습니다.")
        v = [{"what": "테스트", "command": "t", "result": "not_run", "evidence": "", "reason": "환경 없음"}]
        self.assertEqual(check(body=body, verification=v)[0], [])

    def test_english_pass_claim(self):
        body = BODY.replace("통과했습니다", "passed")
        self.assertIn("UNBACKED_PASS_CLAIM", codes(check(body=body, verification=[])[0]))

    def test_not_run_must_be_mentioned_in_body(self):
        v = VERIF + [{"what": "린터", "command": "ruff", "result": "not_run", "evidence": "", "reason": "미설치"}]
        self.assertIn("NOT_RUN_UNMENTIONED", codes(check(verification=v)[0]))
        body = BODY + "\n- 린터는 설치되어 있지 않아 실행하지 못했습니다.\n"
        self.assertEqual(check(body=body, verification=v)[0], [])

    def test_failed_record_is_valid_with_evidence(self):
        v = [{"what": "테스트", "command": "t", "result": "failed", "evidence": "1 failed", "reason": ""}]
        body = BODY.replace("통과했습니다", "일부 실패했습니다")
        self.assertEqual(check(body=body, verification=v)[0], [])


class SecretAndWarningTests(unittest.TestCase):
    def test_secret_in_body_blocks_and_is_not_echoed(self):
        errors, _ = check(body=BODY + "\n키: AKIA" + "Q" * 16 + "\n")
        self.assertIn("SECRET", codes(errors))
        self.assertNotIn("AKIAQQ", json.dumps(errors))

    def test_secret_in_title_blocks(self):
        self.assertIn("SECRET", codes(check(title="fix: " + "ghp_" + "a" * 36)[0]))

    def test_warnings_do_not_block(self):
        body = "## 개요\n이 PR은 할인 계산을 바꿉니다. 자세한 이유와 확인 방법은 이슈를 참고하세요. 길이를 충분히 늘려서 짧다는 경고는 피합니다.\n"
        errors, warnings = check(body=body, verification=[{"what": "x", "command": "c", "result": "passed", "evidence": "ok"}])
        self.assertEqual(errors, [])
        self.assertTrue({"NO_SCOPE", "NO_VERIFICATION_SECTION"} <= set(codes(warnings)))


class ExampleFilesTests(unittest.TestCase):
    """examples/의 파일이 실제 검증기를 통과해야 한다(문서가 거짓이 되지 않도록)."""
    EX = Path(__file__).resolve().parent.parent / "examples"

    def load(self):
        return ((self.EX / "sample-title.txt").read_text(encoding="utf-8").strip(),
                (self.EX / "sample-body.md").read_text(encoding="utf-8"),
                json.loads((self.EX / "sample-claims.json").read_text(encoding="utf-8")),
                json.loads((self.EX / "sample-verification.json").read_text(encoding="utf-8")))

    def test_examples_pass_with_no_errors_and_no_warnings(self):
        title, body, claims, verif = self.load()
        errors, warnings = V.validate(title, body, None, claims, verif)
        self.assertEqual((errors, warnings), ([], []))

    def test_examples_fail_when_the_passed_record_is_removed(self):
        title, body, claims, verif = self.load()
        errors, _ = V.validate(title, body, None, claims, [v for v in verif if v["result"] != "passed"])
        self.assertIn("UNBACKED_PASS_CLAIM", codes(errors))


class CliTests(unittest.TestCase):
    def run_cli(self, title=TITLE, body=BODY, claims=CLAIMS, verification=VERIF, extra=()):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "t.txt").write_text(title, encoding="utf-8")
            (d / "b.md").write_text(body, encoding="utf-8")
            cmd = [sys.executable, str(SCRIPT), "--title-file", str(d / "t.txt"), "--body-file", str(d / "b.md")]
            if claims is not None:
                (d / "c.json").write_text(json.dumps(claims), encoding="utf-8"); cmd += ["--claims", str(d / "c.json")]
            if verification is not None:
                (d / "v.json").write_text(json.dumps(verification), encoding="utf-8"); cmd += ["--verification", str(d / "v.json")]
            return subprocess.run(cmd + list(extra), capture_output=True, text=True)

    def test_ok_exit_zero_and_json(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)["ok"])

    def test_errors_exit_one(self):
        r = self.run_cli(title="")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_missing_file_exit_two(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--title", "x", "--body-file", "/nonexistent/b.md"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_bad_json_exit_two(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); (d / "b.md").write_text(BODY, encoding="utf-8"); (d / "c.json").write_text("{x", encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPT), "--title", TITLE, "--body-file", str(d / "b.md"),
                                "--claims", str(d / "c.json")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
