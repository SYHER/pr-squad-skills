"""사용자 설정(항상 쓸 PR 템플릿)과 템플릿 결정 우선순위 테스트."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support as S
import _common as C

CONFIGURE = S.SCRIPTS / "configure.py"
COLLECT = S.SCRIPTS / "collect_context.py"
VALIDATE = S.SCRIPTS / "validate_pr.py"
CREATE = S.SCRIPTS / "create_pr.py"

USER_TEMPLATE = "## 사용자 개요\n<!-- 설명 -->\n\n## 사용자 확인\n<!-- 확인 -->\n"
REPO_TEMPLATE = "## 저장소 개요\n<!-- 설명 -->\n"
BODY_FOR_USER_TEMPLATE = "## 사용자 개요\n할인율을 적용합니다.\n\n## 사용자 확인\n테스트를 실행하지 못했습니다(환경 없음).\n"
CLAIMS = [{"id": "K1", "kind": "change", "text": "할인율을 적용한다", "evidence": "src/payment.py:2",
           "verdict": "SUPPORTED", "note": ""}]
VERIF = [{"what": "테스트", "command": "t", "result": "not_run", "evidence": "", "reason": "환경 없음"}]


class ConfigBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cfg = self.dir / "cfg" / "config.json"
        self._old = os.environ.get("PR_WRITE_CONFIG")
        os.environ["PR_WRITE_CONFIG"] = str(self.cfg)
        self.user_tpl = self.dir / "user_tpl.md"
        self.user_tpl.write_text(USER_TEMPLATE, encoding="utf-8")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PR_WRITE_CONFIG", None)
        else:
            os.environ["PR_WRITE_CONFIG"] = self._old
        self.tmp.cleanup()

    def write_cfg(self, data):
        self.cfg.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def cli(self, script, *args, cwd=None, extra_env=None):
        env = dict(os.environ, **(extra_env or {}))
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, encoding="utf-8",
                              cwd=cwd, env=env)


class ConfigFileTests(ConfigBase):
    def test_path_prefers_explicit_env_then_xdg_then_home(self):
        self.assertEqual(C.config_path(), self.cfg)
        del os.environ["PR_WRITE_CONFIG"]
        old = os.environ.get("XDG_CONFIG_HOME")
        try:
            os.environ["XDG_CONFIG_HOME"] = "/tmp/xdg"
            self.assertEqual(C.config_path(), Path("/tmp/xdg/pr-write-squad/config.json"))
            del os.environ["XDG_CONFIG_HOME"]
            self.assertEqual(C.config_path(), Path.home() / ".config" / "pr-write-squad" / "config.json")
        finally:
            os.environ["PR_WRITE_CONFIG"] = str(self.cfg)
            if old is not None:
                os.environ["XDG_CONFIG_HOME"] = old

    def test_missing_file_is_empty_config(self):
        self.assertEqual(C.load_config(), {})

    def test_broken_or_wrong_shape_is_an_error(self):
        for bad in ("{not json", "[1, 2]"):
            with self.subTest(bad=bad):
                self.write_cfg(bad)
                with self.assertRaises(C.ConfigError):
                    C.load_config()


class ResolveTemplateTests(ConfigBase):
    def setUp(self):
        super().setUp()
        self.repo = self.dir / "repo"
        (self.repo / ".github").mkdir(parents=True)
        (self.repo / ".github" / "pull_request_template.md").write_text(REPO_TEMPLATE, encoding="utf-8")

    def test_repo_template_is_used_without_config(self):
        path, source, label, _ = C.resolve_template(self.repo)
        self.assertEqual((source, label), ("저장소", ".github/pull_request_template.md"))

    def test_user_config_beats_repo_template(self):
        self.write_cfg({"template": str(self.user_tpl)})
        path, source, _, _ = C.resolve_template(self.repo)
        self.assertEqual((path, source), (self.user_tpl, "사용자 설정"))

    def test_explicit_beats_user_config(self):
        self.write_cfg({"template": str(self.user_tpl)})
        explicit = self.dir / "other.md"
        path, source, _, _ = C.resolve_template(self.repo, explicit=str(explicit))
        self.assertEqual((path, source), (explicit, "지정"))

    def test_disabled_ignores_everything(self):
        self.write_cfg({"template": str(self.user_tpl)})
        self.assertEqual(C.resolve_template(self.repo, disabled=True), (None, None, "", ""))

    def test_nothing_found_means_default_form(self):
        self.assertEqual(C.resolve_template(self.dir / "empty"), (None, None, "", ""))

    def test_configured_file_missing_is_an_error_not_a_silent_fallback(self):
        self.write_cfg({"template": str(self.dir / "gone.md")})
        with self.assertRaises(C.ConfigError) as cm:
            C.resolve_template(self.repo)
        self.assertIn("찾을 수 없습니다", str(cm.exception))

    def test_tilde_in_configured_path_is_expanded(self):
        home_tpl = Path.home() / ".pr_write_squad_test_tpl.md"
        home_tpl.write_text(USER_TEMPLATE, encoding="utf-8")
        try:
            self.write_cfg({"template": "~/.pr_write_squad_test_tpl.md"})
            self.assertEqual(C.resolve_template(self.repo)[0], home_tpl)
        finally:
            home_tpl.unlink()

    def test_template_directory_picks_first_and_notes_the_rest(self):
        d = self.dir / "r2" / ".github" / "PULL_REQUEST_TEMPLATE"
        d.mkdir(parents=True)
        (d / "b.md").write_text("## B\n", encoding="utf-8")
        (d / "a.md").write_text("## A\n", encoding="utf-8")
        path, source, label, note = C.resolve_template(self.dir / "r2")
        self.assertEqual(path.name, "a.md")
        self.assertIn("템플릿이 2개", note)


class ConfigureCliTests(ConfigBase):
    def test_set_stores_absolute_path_and_shows_sections(self):
        r = self.cli(CONFIGURE, "--template", str(self.user_tpl))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("사용자 개요 / 사용자 확인", r.stdout)
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["template"], str(self.user_tpl.resolve()))

    def test_relative_path_is_stored_as_absolute(self):
        r = self.cli(CONFIGURE, "--template", "user_tpl.md", cwd=str(self.dir))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isabs(json.loads(self.cfg.read_text(encoding="utf-8"))["template"]))

    def test_missing_or_headingless_template_is_refused_and_nothing_is_written(self):
        r = self.cli(CONFIGURE, "--template", str(self.dir / "none.md"))
        self.assertEqual(r.returncode, 1)
        (self.dir / "plain.md").write_text("그냥 글\n", encoding="utf-8")
        r = self.cli(CONFIGURE, "--template", str(self.dir / "plain.md"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("섹션 제목", r.stderr)
        self.assertFalse(self.cfg.exists())

    def test_other_settings_are_preserved(self):
        self.write_cfg({"other": 1})
        self.cli(CONFIGURE, "--template", str(self.user_tpl))
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["other"], 1)

    def test_show(self):
        r = self.cli(CONFIGURE, "--show")
        self.assertIn("설정 없음", r.stdout)
        self.cli(CONFIGURE, "--template", str(self.user_tpl))
        r = self.cli(CONFIGURE, "--show")
        self.assertIn(str(self.user_tpl.resolve()), r.stdout)
        self.assertIn("사용자 개요 / 사용자 확인", r.stdout)

    def test_show_warns_when_the_file_has_disappeared(self):
        self.write_cfg({"template": str(self.dir / "gone.md")})
        r = self.cli(CONFIGURE, "--show")
        self.assertEqual(r.returncode, 0)
        self.assertIn("파일이 없습니다", r.stdout)

    def test_clear_removes_the_setting(self):
        self.cli(CONFIGURE, "--template", str(self.user_tpl))
        r = self.cli(CONFIGURE, "--clear")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.cfg.exists())
        self.assertIn("설정이 없습니다", self.cli(CONFIGURE, "--clear").stdout)

    def test_clear_keeps_other_settings(self):
        self.write_cfg({"template": str(self.user_tpl), "other": 2})
        self.cli(CONFIGURE, "--clear")
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8")), {"other": 2})

    def test_broken_config_file_is_reported(self):
        self.write_cfg("{broken")
        self.assertEqual(self.cli(CONFIGURE, "--show").returncode, 2)

    def test_exactly_one_action_is_required(self):
        self.assertEqual(self.cli(CONFIGURE).returncode, 2)
        self.assertEqual(self.cli(CONFIGURE, "--show", "--clear").returncode, 2)


class ToolsUseConfiguredTemplateTests(ConfigBase):
    def setUp(self):
        super().setUp()
        self.work, self.remote = S.make_repo(self.dir / "git")
        S.commit_file(self.work, ".github/pull_request_template.md", REPO_TEMPLATE, "chore: 저장소 템플릿")
        S.git(self.work, "push", "-q", "origin", "main")
        S.git(self.work, "checkout", "-q", "-b", "feat/x")
        S.commit_file(self.work, "src/payment.py", "def t(p, q):\n    return p * q\n", "feat: 합계")

    def test_collect_context_shows_user_template_and_notes_the_repo_one(self):
        self.write_cfg({"template": str(self.user_tpl)})
        r = self.cli(COLLECT, "--repo-dir", str(self.work), "--no-gh")
        self.assertEqual(r.returncode, 0, r.stderr)
        section = r.stdout.split("## pr template")[1].split("## contributing")[0]
        self.assertIn(str(self.user_tpl), section)
        self.assertIn("출처: 사용자 설정 (저장소의 템플릿보다 우선)", section)
        self.assertIn("## 사용자 개요", section)
        self.assertNotIn("## 저장소 개요", section)
        self.assertIn("저장소에도 템플릿(.github/pull_request_template.md)이 있지만 사용자 설정이 우선", section)

    def test_collect_context_falls_back_to_repo_template_without_config(self):
        r = self.cli(COLLECT, "--repo-dir", str(self.work), "--no-gh")
        section = r.stdout.split("## pr template")[1].split("## contributing")[0]
        self.assertIn("경로: .github/pull_request_template.md", section)
        self.assertIn("출처: 저장소", section)

    def test_collect_context_stops_when_configured_file_is_gone(self):
        self.write_cfg({"template": str(self.dir / "gone.md")})
        r = self.cli(COLLECT, "--repo-dir", str(self.work), "--no-gh")
        self.assertEqual(r.returncode, 2)
        self.assertIn("찾을 수 없습니다", r.stderr)

    def write_pr_files(self, body):
        (self.dir / "t.txt").write_text("feat: 합계 추가", encoding="utf-8")
        (self.dir / "b.md").write_text(body, encoding="utf-8")
        (self.dir / "c.json").write_text(json.dumps(CLAIMS), encoding="utf-8")
        (self.dir / "v.json").write_text(json.dumps(VERIF), encoding="utf-8")
        return ["--title-file", str(self.dir / "t.txt"), "--body-file", str(self.dir / "b.md"),
                "--claims", str(self.dir / "c.json"), "--verification", str(self.dir / "v.json")]

    def validate(self, body, *extra):
        return self.cli(VALIDATE, *self.write_pr_files(body), *extra, cwd=str(self.work))

    def test_validate_uses_the_configured_template_automatically(self):
        self.write_cfg({"template": str(self.user_tpl)})
        ok = self.validate(BODY_FOR_USER_TEMPLATE)
        self.assertEqual(ok.returncode, 0, ok.stdout)
        out = json.loads(ok.stdout)
        self.assertEqual(out["template"]["source"], "사용자 설정")
        bad = self.validate("## 저장소 개요\n본문\n")  # 저장소 템플릿 형식으로 쓰면 사용자 설정 위반
        self.assertEqual(bad.returncode, 1)
        self.assertIn("TEMPLATE_SECTION", bad.stdout)

    def test_validate_falls_back_to_the_repo_template(self):
        out = json.loads(self.validate("## 저장소 개요\n본문입니다. 충분히 길게 써서 짧다는 경고를 피합니다. " * 2 + "\n").stdout)
        self.assertEqual(out["template"]["source"], "저장소")

    def test_explicit_template_and_no_template_flags(self):
        self.write_cfg({"template": str(self.user_tpl)})
        explicit = self.dir / "explicit.md"
        explicit.write_text("## 저장소 개요\n", encoding="utf-8")
        r = self.validate("## 저장소 개요\n내용이 있습니다.\n", "--template", str(explicit))
        self.assertEqual(json.loads(r.stdout)["template"]["source"], "지정")
        r = self.validate("그냥 본문입니다. 테스트는 실행하지 못했습니다(환경 없음). 길게 쓰면 경고도 없을 거예요. " * 2,
                          "--no-template")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIsNone(json.loads(r.stdout)["template"]["path"])

    def test_validate_reports_a_missing_configured_file_as_an_input_error(self):
        self.write_cfg({"template": str(self.dir / "gone.md")})
        self.assertEqual(self.validate(BODY_FOR_USER_TEMPLATE).returncode, 2)

    def test_create_pr_enforces_the_configured_template_before_touching_anything(self):
        self.write_cfg({"template": str(self.user_tpl)})
        exe, env, log = S.make_fake_gh(self.dir, [
            {"match": ["repo view"], "stdout": json.dumps({"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}})},
            {"match": ["pr list"], "stdout": "[]"}])
        args = ["--repo-dir", str(self.work), *self.write_pr_files("## 저장소 개요\n저장소 형식으로 쓴 본문입니다.\n")]
        r = self.cli(CREATE, *args, "--post", extra_env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("TEMPLATE_SECTION", r.stderr)
        self.assertEqual(S.read_calls(log), [])
        self.assertEqual(S.git(self.remote, "branch", "--list", "feat/x"), "")

    def test_create_pr_preview_names_the_template_it_used(self):
        self.write_cfg({"template": str(self.user_tpl)})
        exe, env, log = S.make_fake_gh(self.dir, [
            {"match": ["repo view"], "stdout": json.dumps({"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}})},
            {"match": ["pr list"], "stdout": "[]"}])
        args = ["--repo-dir", str(self.work), *self.write_pr_files(BODY_FOR_USER_TEMPLATE)]
        r = self.cli(CREATE, *args, extra_env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"템플릿: {self.user_tpl} (출처: 사용자 설정)", r.stdout)


if __name__ == "__main__":
    unittest.main()
