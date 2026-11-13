"""根目录与技能 assets 的镜像一致性。

技能发出去的是 `skills/vibe-base/assets/`，维护时改的是仓库根的同名文件。
两者漂移时，新项目装到的就是旧版本，而且没有任何报错——所以这条不变量必须有测试，
不能只靠 README 里那段手工 md5 循环。

例外只有一类：状态类文件（记忆快照、交接、三份索引）。根是本仓实况，模板必须是占位，
否则每个新项目初始化后都会继承别的项目的会话状态。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "skills" / "vibe-base" / "assets"

sys.path.insert(0, str(ROOT / "skills" / "vibe-base" / "scripts"))
from init_vibe_project import is_scaffold_file  # noqa: E402 — 复用脚手架自己的"什么算骨架"判据

STATE_FILES = (
    "docs/memory/context.md",
    "docs/memory/handoff.md",
    "docs/decisions/README.md",
    "docs/specs/README.md",
    "docs/design/README.md",
)


def asset_files() -> list[str]:
    """骨架里真正会发出去的文件：与 init 用同一份判据，缓存不参与镜像比对。"""
    return sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*") if is_scaffold_file(p))


class TestMirror(unittest.TestCase):
    def test_root_matches_assets(self) -> None:
        drift = []
        for rel in asset_files():
            if rel in STATE_FILES:
                continue
            root_file = ROOT / rel
            if not root_file.exists():
                drift.append(f"根缺 {rel}")
            elif root_file.read_bytes() != (ASSETS / rel).read_bytes():
                drift.append(f"内容不一致 {rel}")
        self.assertEqual(drift, [], "根与 assets 漂移（改完一侧要同步另一侧）：" + "; ".join(drift))

    def test_root_governance_docs_are_all_shipped(self) -> None:
        """新写的治理细则必须同时进 assets，否则项目装不到它。"""
        shipped = set(asset_files())
        extra = [p.relative_to(ROOT).as_posix()
                 for p in sorted((ROOT / "docs" / "governance").glob("*.md"))
                 if p.relative_to(ROOT).as_posix() not in shipped]
        self.assertEqual(extra, [], f"根有而骨架不发：{extra}")

    def test_state_files_stay_placeholder(self) -> None:
        """状态类文件的模板必须留占位：被实况覆盖就等于把本仓状态发给所有新项目。"""
        for rel in STATE_FILES:
            path = ASSETS / rel
            self.assertTrue(path.exists(), f"模板缺 {rel}")
            body = path.read_text(encoding="utf-8")
            self.assertRegex(body, r"<|YYYY-MM-DD", f"{rel} 的模板里没有占位符，疑似被实况覆盖")

    def test_state_files_are_exactly_the_known_set(self) -> None:
        """反向守卫：真有实况与模板必须不同的新文件时，必须显式登记进 STATE_FILES。"""
        differing = {rel for rel in asset_files()
                     if (ROOT / rel).exists()
                     and (ROOT / rel).read_bytes() != (ASSETS / rel).read_bytes()}
        self.assertEqual(differing, set(STATE_FILES),
                         f"实况与模板不同的文件集合变了：{sorted(differing)}；"
                         f"若是有意为之，登记进 STATE_FILES 并说明理由")


if __name__ == "__main__":
    unittest.main()
