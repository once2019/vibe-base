"""init_vibe_project.py 的脚手架测试：文件落盘、.vibe 骨架、非 git 目录、幂等复跑。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "vibe-base" / "scripts" / "init_vibe_project.py"


def run_script(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(target)],
                          capture_output=True, text=True)


def git(target: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=target, check=True, capture_output=True)


class TestScaffoldFileFilter(unittest.TestCase):
    """工具缓存不是骨架内容：在 assets 里跑一次 ruff/pytest 就会生成，不能跟着复制进项目。"""

    def test_cache_paths_are_excluded(self) -> None:
        sys.path.insert(0, str(SCRIPT.parent))
        import init_vibe_project as scaffold
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            keep = [root / "AGENTS.md", root / "scripts" / "foreman.py"]
            drop = [root / "scripts" / ".ruff_cache" / "CACHEDIR.TAG",
                    root / "scripts" / "__pycache__" / "x.pyc",
                    root / "docs" / ".mypy_cache" / "cache.json",
                    root / ".git" / "config"]
            for f in keep + drop:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("x\n", encoding="utf-8")
            for f in keep:
                self.assertTrue(scaffold.is_scaffold_file(f), f)
            for f in drop:
                self.assertFalse(scaffold.is_scaffold_file(f), f)

    def test_skipped_asset_files_reports_dotfiles(self) -> None:
        """点文件被过滤时必须能被报告出来，否则将来放 .editorconfig 会静默失效。"""
        sys.path.insert(0, str(SCRIPT.parent))
        import init_vibe_project as scaffold
        known = {p.name for p in scaffold.skipped_asset_files()}
        self.assertNotIn("foreman.py", known)
        for p in scaffold.skipped_asset_files():
            self.assertFalse(scaffold.is_scaffold_file(p))
            self.assertFalse(any(x in p.parts for x in ("__pycache__", ".ruff_cache")),
                             f"缓存不该出现在报告里：{p}")

    def test_copy_scope_excludes_caches(self) -> None:
        """守卫：复制范围里不许出现缓存（曾因在 assets/scripts 里跑 ruff 而把 .ruff_cache 发进项目）。"""
        sys.path.insert(0, str(SCRIPT.parent))
        import init_vibe_project as scaffold
        bad = [p.as_posix() for p in scaffold.collect_asset_files()
               if any(x in p.parts for x in ("__pycache__", ".ruff_cache",
                                             ".mypy_cache", ".pytest_cache"))]
        self.assertEqual(bad, [], f"复制范围里混进了缓存：{bad}")


class TestInitScaffold(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="vibe-init-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.root)]))

    def test_git_repo_gets_scaffold_and_vibe_skeleton(self) -> None:
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "init")

        proc = run_script(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        self.assertTrue((self.root / "AGENTS.md").exists())
        self.assertTrue((self.root / "scripts" / "foreman.py").exists())
        cfg = self.root / ".vibe" / "config.json"
        self.assertTrue(cfg.exists(), f"init 应建 .vibe 骨架：{proc.stdout}")
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["base"], "main")
        for sub in ("tasks", "worktrees", "archive", "logs"):
            self.assertTrue((self.root / ".vibe" / sub).is_dir(), sub)
        exclude = (self.root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(".vibe", exclude)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.root,
                                capture_output=True, text=True).stdout
        self.assertNotIn(".vibe", status)

    def test_non_git_dir_skips_state_with_hint(self) -> None:
        proc = run_script(self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.root / "AGENTS.md").exists())
        self.assertFalse((self.root / ".vibe").exists())
        self.assertIn("还不是 git 仓库", proc.stdout)

    def test_stale_control_script_is_reported(self) -> None:
        """项目副本与模板不同：不覆盖，但必须提示，否则会长期跑旧控制面。"""
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "init")
        self.assertEqual(run_script(self.root).returncode, 0)
        (self.root / "scripts" / "foreman.py").write_text("# 旧版控制面\n", encoding="utf-8")

        proc = run_script(self.root)
        self.assertIn("新建 0 个文件", proc.stdout)
        self.assertIn("与技能模板不一致", proc.stdout)
        self.assertEqual((self.root / "scripts" / "foreman.py").read_text(encoding="utf-8"),
                         "# 旧版控制面\n", "不覆盖既有副本")

    def test_identical_control_script_is_silent(self) -> None:
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "init")
        self.assertEqual(run_script(self.root).returncode, 0)
        self.assertNotIn("与技能模板不一致", run_script(self.root).stdout)

    def test_rerun_is_idempotent(self) -> None:
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "init")
        self.assertEqual(run_script(self.root).returncode, 0)
        cfg_before = (self.root / ".vibe" / "config.json").read_text(encoding="utf-8")

        second = run_script(self.root)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("新建 0 个文件", second.stdout)
        self.assertEqual((self.root / ".vibe" / "config.json").read_text(encoding="utf-8"), cfg_before)


if __name__ == "__main__":
    unittest.main()
