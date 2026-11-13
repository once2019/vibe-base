"""根目录与技能 assets 的关系：该是链接的必须是链接，该独立的必须留占位。

正本只有一份（`skills/vibe-base/assets/`）。根目录同名文件是指向它的符号链接，
改哪边都是同一份文件，不存在"改完忘了同步"的漂移。

例外是状态类文件：根是本仓实况，模板必须是占位，否则每个新项目初始化后都会继承
别的项目的会话状态。它们必须是独立文件，链接过去就等于把实况发给所有新项目。
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "skills" / "vibe-base" / "assets"

STATE_FILES = (
    "docs/memory/context.md",
    "docs/memory/handoff.md",
    "docs/decisions/README.md",
    "docs/specs/README.md",
    "docs/design/README.md",
)


def asset_files() -> list[str]:
    """骨架里真正会发出去的文件（缓存不参与）。"""
    return sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


class TestMirrorLinks(unittest.TestCase):
    def test_shipped_files_are_symlinks(self) -> None:
        """除状态类文件外，根同名文件必须是指向 assets 的符号链接。

        有人把它复制成实体文件就会开始漂移：技能发出去的是旧版，且没有任何报错。
        """
        wrong = []
        for rel in asset_files():
            if rel in STATE_FILES:
                continue
            link = ROOT / rel
            if not link.is_symlink():
                wrong.append(f"{rel}（不是链接）")
            elif link.resolve() != (ASSETS / rel).resolve():
                wrong.append(f"{rel}（指向 {link.resolve()}）")
        self.assertEqual(wrong, [], "这些文件应是指向 assets 的符号链接：" + "; ".join(wrong))

    def test_state_files_are_independent_and_placeholder(self) -> None:
        for rel in STATE_FILES:
            root_file, template = ROOT / rel, ASSETS / rel
            self.assertFalse(root_file.is_symlink(), f"{rel} 不能链接到模板")
            self.assertTrue(template.exists(), f"模板缺 {rel}")
            self.assertRegex(template.read_text(encoding="utf-8"), r"<|YYYY-MM-DD",
                             f"{rel} 的模板里没有占位符，疑似被实况覆盖")

    def test_governance_docs_are_all_shipped(self) -> None:
        """新写的治理细则必须同时进 assets，否则项目装不到它。"""
        shipped = set(asset_files())
        extra = [p.relative_to(ROOT).as_posix()
                 for p in sorted((ROOT / "docs" / "governance").glob("*.md"))
                 if p.relative_to(ROOT).as_posix() not in shipped]
        self.assertEqual(extra, [], f"根有而骨架不发：{extra}")


if __name__ == "__main__":
    unittest.main()
