"""技能包只发规范与原则：规范文档里不得出现论据、推导与历史过程。

判据是文本形态——带日期的实测记述、事故编号、代价陈述。原因与证据的正本是
`docs/decisions/`（技能仓自身）与项目内的决策记录，规范文档只留做法与判据。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "vibe-base"

# 只在规范文档里判：代码注释与决策记录允许讲原因
RATIONALE = re.compile(r"\d{4}-\d{2}-\d{2} 实测|实测 [Tw]-|代价是|这解释|不点名")


def norm_docs() -> list[Path]:
    docs = [SKILL / "SKILL.md", SKILL / "README.md"]
    docs += sorted((SKILL / "assets").rglob("*.md"))
    return docs


class TestSkillDocsCarryOnlyNorms(unittest.TestCase):
    def test_no_rationale_in_normative_docs(self) -> None:
        hits = []
        for path in norm_docs():
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if RATIONALE.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:70]}")
        self.assertEqual([], hits, "规范文档里出现论据/过程记述：\n" + "\n".join(hits))

    def test_scanned_docs_are_not_empty(self) -> None:
        docs = norm_docs()
        self.assertGreaterEqual(len(docs), 12)
        self.assertTrue(any("AGENTS.md" in str(d) for d in docs))


if __name__ == "__main__":
    unittest.main()
