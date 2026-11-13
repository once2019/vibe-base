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
