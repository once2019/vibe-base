"""类型检查门禁：技能脚本过 mypy。

foreman.py 与 init_vibe_project.py 是纯标准库单文件，storyboard 等项目把它们
复制过去后会被项目自身的 mypy 扫到；本仓不跑 mypy 的话类型回归要等项目门禁才暴露。
mypy 按 --user 安装（属 AGENTS.md §4 的系统级依赖），没装就跳过并提示。
"""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "vibe-base" / "assets" / "scripts"
ROOT = Path(__file__).resolve().parent.parent

FILES = [SCRIPTS / "foreman.py", SCRIPTS / "init_vibe_project.py",
         ROOT / "skills" / "vibe-base" / "scripts" / "init_vibe_project.py"]


class TypeCheck(unittest.TestCase):
    def test_mypy_clean(self) -> None:
        if shutil.which("mypy") is None and not Path(sys.executable).with_name("mypy").exists():
            self.skipTest("mypy 未安装（--user 安装后重跑；属系统级依赖）")
        real = [f for f in FILES if f.exists() and not f.is_symlink()]
        self.assertTrue(real, "找不到可检查的实体脚本（镜像应全是指向 assets 的链接）")
        proc = subprocess.run([sys.executable, "-m", "mypy", *[str(f) for f in real]],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0,
                         f"mypy 报错（真错误或依赖缺失都算失败，fail-visible）：\n{proc.stdout}{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
