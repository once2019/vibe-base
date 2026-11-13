#!/usr/bin/env python3
"""vibe-base 脚手架：将治理骨架（AGENTS.md + docs/）创建到目标仓库。

幂等：默认不覆盖已存在的文件（--force 显式覆盖）。
运行后扫描骨架文件（含已存在的，格式示例文件除外）中的 `<...>` 占位符并按 文件:行 报告，
作为回填进度参考；复跑可查看剩余待回填项。
退出码：0=流程完成（含全部跳过）；2=skill assets 缺失或为空。
.vibe/ 过程态骨架随初始化一并建好：在 git 仓库里调用刚落地的 scripts/foreman.py init（幂等）。

"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
GOVERNANCE_PREFIX = "docs/governance/"

# 这些文件本身是"格式的模板"（供未来复制使用），其占位符无需填写
FORMAT_EXAMPLES = (
    "docs/decisions/_template.md",
    "docs/decisions/README.md",
    "docs/design/README.md",
    "docs/progress/README.md",
)

# 程序文件不是模板：其中的 <...> 是代码/文档里的字面量，不参与回填扫描
CODE_PREFIXES = ("scripts/",)

# 技能自带的参考文档（随 init 发布、内容不随项目变化）：其中的 <taskId> 之类是命令字面量，不是待填项
REFERENCE_DOCS = (
    "docs/governance/dispatch-template.md",
    "docs/governance/orchestration-doctrine.md",
    "docs/governance/orchestration-orca.md",
)

PLACEHOLDER_RE = re.compile(r"<([^<>\n]+)>")


def bootstrap_state(target: Path) -> tuple[bool, str]:
    """把 .vibe/ 过程态骨架一并建好：调用随骨架落地的 scripts/foreman.py init（幂等）。

    只在 git 仓库里做：foreman.py init 要读 base 分支，非 git 目录给提示后跳过。
    骨架与能力启用是两件事：目录先备好，不派工、不探测通道。
    """
    script = target / "scripts" / "foreman.py"
    if not (target / ".git").exists():
        return False, "跳过 .vibe/ 骨架：目标还不是 git 仓库（git init 后跑 scripts/foreman.py init）"
    if not script.exists():
        return False, "跳过 .vibe/ 骨架：scripts/foreman.py 未落地"
    proc = subprocess.run([sys.executable, str(script), "init", "--repo", str(target)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout).strip().splitlines()
        return False, f".vibe/ 骨架未建：{reason[-1] if reason else f'exit={proc.returncode}'}"
    first = (proc.stdout or "").strip().splitlines()
    return True, first[0] if first else ".vibe/ 已初始化"


# 工具缓存不是骨架内容：在 assets 目录里跑一次 ruff/pytest 就会生成，别跟着复制进项目
CACHE_PARTS = {"__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache", ".git"}


def is_scaffold_file(path: Path) -> bool:
    return (path.is_file() and path.suffix != ".pyc"
            and not any(part in CACHE_PARTS or part.startswith(".") for part in path.parts))


def collect_asset_files():
    return sorted(p for p in ASSETS_DIR.rglob("*") if is_scaffold_file(p))


def skipped_asset_files() -> list[Path]:
    """assets 里被过滤掉的文件（缓存之外的点文件）：不该静默消失。"""
    return sorted(p for p in ASSETS_DIR.rglob("*")
                  if p.is_file() and not is_scaffold_file(p)
                  and not any(part in CACHE_PARTS or part == "__pycache__" for part in p.parts))


def copy_scaffold(target: Path, force: bool):
    created, skipped = [], []
    for src in collect_asset_files():
        dst = target / src.relative_to(ASSETS_DIR)
        if dst.exists() and not force:
            skipped.append(dst)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        created.append(dst)
    return created, skipped


def scan_placeholders(target: Path, created):
    findings = []
    for dst in created:
        rel = dst.relative_to(target).as_posix()
        if (rel in FORMAT_EXAMPLES or rel in REFERENCE_DOCS
                or rel.startswith(GOVERNANCE_PREFIX) or rel.startswith(CODE_PREFIXES)):
            continue
        for lineno, line in enumerate(dst.read_text(encoding="utf-8").splitlines(), 1):
            for m in PLACEHOLDER_RE.finditer(line):
                if m.group(1).strip() == "...":  # 模板中 `<...>` 为占位符的元说明
                    continue
                findings.append((rel, lineno, m.group(0)))
                break  # 每行报一处即可
    return findings


CONTROL_SCRIPT = "scripts/foreman.py"


def drift_note(target: Path, skipped: list[Path]) -> str:
    """已存在但与模板不同的控制面脚本：不覆盖，但要提示，否则项目会长期跑旧版。"""
    if not any(p.as_posix() == str(target / CONTROL_SCRIPT) for p in skipped):
        return ""
    src, dst = ASSETS_DIR / CONTROL_SCRIPT, target / CONTROL_SCRIPT
    try:
        if src.read_bytes() == dst.read_bytes():
            return ""
        version = re.search(r'^VERSION = "([^"]+)"', src.read_text(encoding="utf-8"), re.M)
    except OSError:
        return ""
    tag = f"模板版本 {version.group(1)}，" if version else ""
    return f"!! {CONTROL_SCRIPT} 与技能模板不一致（{tag}未覆盖）：需要更新时用 --force 或手工替换"


def main():
    parser = argparse.ArgumentParser(
        description="初始化 vibe coding 治理骨架（AGENTS.md + docs/）"
    )
    parser.add_argument("target", nargs="?", default=".",
                        help="目标仓库根目录（默认当前目录）")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已存在的目标文件（默认跳过）")
    args = parser.parse_args()

    if not ASSETS_DIR.is_dir() or not collect_asset_files():
        print(f"错误：assets 缺失或为空（{ASSETS_DIR}），skill 结构不完整",
              file=sys.stderr)
        return 2

    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    created, skipped = copy_scaffold(target, args.force)

    print(f"目标：{target}")
    print(f"新建 {len(created)} 个文件：")
    for p in created:
        print(f"  + {p.relative_to(target).as_posix()}")
    if skipped:
        print(f"跳过 {len(skipped)} 个已存在文件（未改动；需覆盖用 --force）：")
        for p in skipped:
            print(f"  = {p.relative_to(target).as_posix()}")

    findings = scan_placeholders(target, created + skipped)
    if findings:
        print("待填占位符（文件:行 -> 内容）：")
        for rel, lineno, text in findings:
            print(f"  {rel}:{lineno} -> {text}")
    else:
        print("无待填占位符。")

    ignored = skipped_asset_files()
    if ignored:
        print("注意：以下 assets 文件不参与复制（点开头/缓存规则）："
              + ", ".join(p.relative_to(ASSETS_DIR).as_posix() for p in ignored))

    drift = drift_note(target, skipped)
    if drift:
        print(drift)

    ok, note = bootstrap_state(target)
    print(note if ok else f"!! {note}")

    print("下一步：骨架生成完毕。占位符 <...> 无需当场追问，由 Agent 在后续开发中获知实际信息时主动回填；上述清单即回填进度参考。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
