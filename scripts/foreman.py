#!/usr/bin/env python3
"""foreman.py — vibe-base 执行编排控制面 CLI（仅 Python 标准库）。

条文正本：仓库 docs/governance/orchestration.md；理念：docs/governance/orchestration-doctrine.md。
层级固定：编排通道（hcom/Orca）作事件层、git worktree 作隔离层、.vibe/ 作过程态层。
过程态不入库、随会话消失；归档后事实源是 docs/progress/ 与 docs/decisions/。
所有通道调用收口在 Transport 实现类；过程态唯一事实源是 tasks/<id>/task.json，写入原子化。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "1.0.0"  # 融入 vibe-base 的契约版本：过程态 .vibe/、docs 落点登记、Ask-first 决策记录门槛

EXIT_OK = 0
EXIT_ERROR = 1

HEADER_RE = re.compile(r"^\[FOREMAN:([A-Z_]+)\]\s+task=(\S+)\s+dispatch=(\S+)", re.M)

# 任务状态机（见 docs/specs/vibe-foreman-skill.md）。任意状态 → blocked 由 set_status 单独放行。
# 构建/测试产物：写进 .git/info/exclude（本地生效，不入库），避免 merge 复跑验收后
# 主仓库出现未跟踪噪音挡住下一次合并。
LOG_MAX_BYTES = 1_000_000  # 追加型日志超过 1MB 轮转一份 .1，避免无界增长

BUILD_NOISE = ("__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
               "node_modules/", ".venv/", "target/", "dist/", "build/")

# docs 落点：显式登记而非推断（AGENTS.md §3），config.docs 可覆盖
DEFAULT_DOCS = {"progress": "docs/progress", "decisions": "docs/decisions"}

# task.json 状态 → docs/progress 完成状态（两套词表显式对齐，避免"进行中"两种说法）
STATUS_CN = {"completed": "已完成", "blocked": "已阻塞"}
DEFAULT_STATUS_CN = "进行中"


def docs_paths(cfg: dict) -> dict:
    """返回 {progress, decisions} 相对路径；缺省取模板约定。"""
    d = cfg.get("docs") or {}
    return {k: str(d.get(k) or v) for k, v in DEFAULT_DOCS.items()}


def progress_rel_for(cfg: dict, task: dict) -> str:
    """竣工单落点：已登记优先；缺省带任务 id 保证唯一（同一天同名任务不得互相覆盖）。"""
    registered = (task.get("docs") or {}).get("progress")
    if registered:
        return str(registered)
    slug = task.get("slug") or slugify(str(task.get("title") or "task"))
    return f"{docs_paths(cfg)['progress']}/{now_iso()[:10]}-{slug}-{task['id']}.md"

TRANSITIONS: dict[str, set[str]] = {
    "pending": {"ready", "blocked"},
    "ready": {"dispatched", "blocked"},
    "dispatched": {"running", "blocked"},
    "running": {"waiting_answer", "waiting_human", "review", "failed", "blocked"},
    "waiting_answer": {"running", "waiting_human", "failed", "blocked"},
    "waiting_human": {"running", "failed", "blocked"},
    "review": {"merge_ready", "failed", "blocked"},
    "failed": {"dispatched", "blocked"},
    "merge_ready": {"completed", "blocked"},
    "completed": set(),
    "blocked": {"dispatched"},
}

WORKER_STATUSES = {"waiting_answer", "waiting_human"}  # 这些状态下 worker 在等主管回复

SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("openai key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("slack token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("assigned credential", re.compile(r"(?i)(password|secret|token)\s*[:=]\s*['\"][^'\"]{8,}")),
]

SKIP_MARKERS = [
    "--no-verify",
    "pytest.skip",
    "@unittest.skip",
    "it.skip(",
    "test.skip(",
    "describe.skip(",
    "t.skip(",
]


# ---------------------------------------------------------------- 基础工具


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def fail(msg: str, code: int = EXIT_ERROR) -> "NoReturn":
    eprint(f"Error: {msg}")
    sys.exit(code)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def run_cmd(args: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    """所有外部命令一律参数列表调用，禁止 shell=True。

    超时统一降级成退出码 124 的回执，不抛 `TimeoutExpired`：调用方已有 fail-visible 分支
    （daemon 卡住时应该看到"失败原因"，而不是 Python 栈）。"""
    try:
        return subprocess.run(
            args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        def as_text(v: object) -> str:
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            return str(v or "")
        return subprocess.CompletedProcess(args, 124, as_text(exc.stdout),
                                           f"超时 {timeout}s 未返回：{args[0]}")


def out_or_fail(proc: subprocess.CompletedProcess, what: str) -> str:
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        fail(f"{what} 失败（exit {proc.returncode}）：{detail}")
    return proc.stdout


def header_candidates(text: str) -> list[tuple[str, str, str]]:
    """取出文本里全部协议头（去重保序）。同一封里出现多个不同协议头属于歧义，调用方必须失败。"""
    seen: list[tuple[str, str, str]] = []
    for m in HEADER_RE.finditer(text or ""):
        cand = (m.group(1), m.group(2), m.group(3))
        if cand not in seen:
            seen.append(cand)
    return seen


def parse_header(text: str) -> tuple[str, str, str] | None:
    """解析消息首行 [FOREMAN:<TYPE>] task=<id> dispatch=<id>（允许同行跟正文）；无协议头返回 None。"""
    cands = header_candidates(text)
    return cands[0] if cands else None


def safe_name(raw: object, limit: int = 40) -> str:
    """外部传入的标识（发送者名）不得直接拼进文件路径。"""
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(raw or "?"))[:limit] or "?"


def parse_scopes(values: list[str] | None) -> list[str]:
    """把可重复的 --scope（每个可含逗号）摊平去重，保持顺序。"""
    out: list[str] = []
    for raw in values or []:
        for part in str(raw).split(","):
            item = part.strip()
            if item and item not in out:
                out.append(item)
    return out


def slugify(title: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", title).strip("-").lower()
    return (slug[:40] or "task").rstrip("-")


def append_log(path: Path, text: str) -> None:
    """追加一行/多行日志；超过 LOG_MAX_BYTES 先轮转到 .1（只留一份历史）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size >= LOG_MAX_BYTES:
        path.replace(path.with_suffix(path.suffix + ".1"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")


def emit(args: argparse.Namespace, data: dict, human: str) -> None:
    """--json 输出机器可读 JSON，否则输出人类可读摘要。"""
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(human)


# ---------------------------------------------------------------- 仓库与状态层


class Repo:
    """目标仓库内 .vibe/ 过程态层的读写入口。"""

    def __init__(self, repo: Path):
        self.root = repo.resolve()
        self.foreman = self.root / ".vibe"
        self.tasks_dir = self.foreman / "tasks"
        self.worktrees_dir = self.foreman / "worktrees"
        self.archive_dir = self.foreman / "archive"
        self.logs_dir = self.foreman / "logs"
        self.config_path = self.foreman / "config.json"

    # ---- git ----

    def git(self, *args: str, cwd: Path | None = None) -> str:
        proc = run_cmd(["git", *args], cwd=cwd or self.root)
        return out_or_fail(proc, f"git {args[0]}")

    def git_try(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return run_cmd(["git", *args], cwd=cwd or self.root)

    def is_git_repo(self) -> bool:
        return self.git_try("rev-parse", "--is-inside-work-tree").returncode == 0

    def current_branch(self) -> str:
        proc = self.git_try("rev-parse", "--abbrev-ref", "HEAD")
        if proc.returncode != 0:
            return ""  # 空 HEAD（无首次提交，分支未出生）
        return proc.stdout.strip()

    def is_clean(self) -> bool:
        return self.git_try("status", "--porcelain").stdout.strip() == ""

    # ---- 配置 ----

    def config(self) -> dict:
        cfg = read_json(self.config_path)
        base = cfg.get("base")
        if not base:
            fail(f"config.json 缺少 base 字段：{self.config_path}")
        return cfg

    def load_or_default_config(self) -> dict:
        branch = self.current_branch() if self.is_git_repo() else ""
        defaults = {
            "foreman_name": "foreman",
            "tag": "foreman",
            "default_agent": "codex",
            "base": branch or "main",
            "max_attempts": 3,
            "require_human_review": False,
            "hcom_bin": "hcom",
            "docs": dict(DEFAULT_DOCS),
        }
        if not self.config_path.exists():
            return defaults
        cfg = read_json(self.config_path)
        merged = {**defaults, **cfg}
        merged["docs"] = {**defaults["docs"], **(cfg.get("docs") or {})}
        return merged

    # ---- 任务 ----

    def task_dir(self, task_id: str, archived: bool = False) -> Path:
        base = self.archive_dir if archived else self.tasks_dir
        return base / task_id

    def find_task_dir(self, task_id: str) -> Path:
        for base in (self.tasks_dir, self.archive_dir):
            d = base / task_id
            if (d / "task.json").exists():
                return d
        fail(f"任务不存在：{task_id}")

    def load_task(self, task_id: str) -> dict:
        task = read_json(self.find_task_dir(task_id) / "task.json")
        if task.get("id") != task_id:
            fail(f"task.json 的 id ({task.get('id')}) 与请求的 {task_id} 不一致")
        return task

    def save_task(self, task: dict) -> None:
        task["updated_at"] = now_iso()
        write_json(self.find_task_dir(task["id"]) / "task.json", task)

    def log_task(self, task: dict, event: str, detail: str) -> None:
        task.setdefault("log", []).append({"at": now_iso(), "event": event, "detail": detail})

    def set_status(self, task: dict, new_status: str) -> None:
        old = task["status"]
        if new_status == old:
            return  # 重复协议消息（如重发 DONE）幂等 no-op
        allowed = TRANSITIONS.get(old, set()) | ({"blocked"} if new_status == "blocked" else set())
        if new_status not in allowed:
            fail(f"非法状态迁移 {old} → {new_status}（任务 {task['id']}）")
        task["status"] = new_status

    def next_task_id(self) -> str:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        nums = [int(p.name.split("-")[1]) for p in self.tasks_dir.iterdir() if re.match(r"T-\d+$", p.name)]
        nums += [int(p.name.split("-")[1]) for p in self.archive_dir.glob("T-*")] if self.archive_dir.exists() else []
        return f"T-{max(nums, default=0) + 1:03d}"

    def next_dispatch_id(self, task: dict) -> str:
        return f"D-{len(task.get('dispatches', [])) + 1:03d}"


# ---------------------------------------------------------------- 编排 Transport 层


AGENT_BINARIES = ("pi", "codex", "claude", "gemini", "opencode", "kilo", "omp", "hermes",
                  "cursor", "kimi", "copilot", "qoder", "grok")
RUNTIME_COMMS = {"node", "bun", "deno", "python"}  # python3.14 之类按前缀识别，见 is_runtime_comm()

DETECT_TIMEOUT = 15  # 运行时探测是旁路判断：daemon 卡住时不能拖住主命令

AGENT_ENV_MARKERS = (
    ("pi", ("PI_CODING_AGENT", "PI_SESSION_ID")),
    ("codex", ("CODEX_THREAD_ID",)),
    ("claude", ("CLAUDECODE",)),
)


def ancestor_commands(limit: int = 16) -> list[str]:
    """由近到远返回当前进程的祖先命令（comm + cmdline 拼接）。/proc 不可用（如 macOS）返回空。

    单独抽出来是为了可测：真实 /proc 不可注入。"""
    if not Path("/proc/self/status").exists():
        return []
    out: list[str] = []
    pid = os.getpid()
    for _ in range(limit):
        try:
            status = Path(f"/proc/{pid}/status").read_text()
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            cmdline = Path(f"/proc/{pid}/cmdline").read_text().replace("\0", " ").strip()
        except OSError:
            break
        out.append(f"{comm}\t{cmdline}")
        m = re.search(r"^PPid:\s*(\d+)", status, re.M)
        if not m or int(m.group(1)) <= 1:
            break
        pid = int(m.group(1))
    return out


def is_runtime_comm(comm: str) -> bool:
    """宿主运行时（node/bun/deno/python/python3.x）——它们的 comm 不是 agent 名，得看 cmdline。"""
    return comm in RUNTIME_COMMS or bool(re.fullmatch(r"python3(\.\d+)?", comm))


def agent_from_commands(commands: list[str]) -> str | None:
    """从祖先命令里认出最近的 agent 进程。

    - comm 直接是 agent 二进制（pi 的 comm 就是 `pi`）
    - 或 comm 是 node/bun/python 这类运行时，而 cmdline 里带着该 agent 的包路径
      （Claude Code 是 `node …/claude-code/cli.js`，comm 只会是 node）"""
    for row in commands:
        comm, _, cmdline = row.partition("\t")
        comm = Path(comm.strip()).name
        if comm in AGENT_BINARIES:
            return comm
        if is_runtime_comm(comm):
            for name in AGENT_BINARIES:
                if re.search(rf"(^|/){name}([-.@/]|$)|/{name}-code(/|$)", cmdline):
                    return name
    return None


def detect_session_agent() -> str | None:
    """当前主管会话是哪个 agent。优先读进程祖先链（继承不来、不撒谎），再退化到环境标记。

    环境标记只认该 agent 运行时自己设的变量；Orca 全局注入的 CLAUDE_CODE_* / ORCA_* 不能作为证据，
    且父会话的环境会被子会话继承（pi 里起 codex 会带着 PI_*），所以进程树优先。"""
    # 跳过第 0 条（自己）：命令行里可能有 `--agent pi` 之类的参数，不能自证
    return agent_from_commands(ancestor_commands()[1:]) or next(
        (agent for agent, keys in AGENT_ENV_MARKERS if any(os.environ.get(k) for k in keys)), None)


def resolve_worker_agent(cfg: dict, task: dict, explicit: str | None,
                         session_agent: str | None) -> tuple[str, str]:
    """worker agent 优先级：--agent > task.json 显式登记 > 与主管同一 agent > config 兜底。

    返回 (agent, 来源)，来源写进 dispatch 记录以便审计。"""
    for value, source in ((explicit, "命令行 --agent"), (task.get("agent"), "任务登记"),
                          (session_agent, "主管会话"), (cfg.get("default_agent"), "config 兜底")):
        if value:
            if shutil.which(value) is None:
                fail(f"worker agent `{value}` 不在 PATH（来源：{source}）；用 --agent 指定已安装的 agent")
            return value, source
    fail("没有可用的 worker agent：传 --agent，或 init --agent 设置兜底")


def resolve_orca_bin() -> str:
    """按 orca-cli 契约解析 CLI：ORCA_CLI_COMMAND → dev checkout 的 orca-dev → orca。"""
    env = os.environ.get("ORCA_CLI_COMMAND")
    if env:
        return env
    if os.environ.get("ORCA_DEV_REPO_ROOT") and shutil.which("orca-dev"):
        return "orca-dev"
    return "orca"


class Transport:
    """编排底座接口：消息/拉起/等待/身份。实现：Hcom（默认）与 Orca。"""

    def available(self) -> bool:
        raise NotImplementedError

    def self_identity(self) -> dict | None:
        raise NotImplementedError

    def send(self, target: str, text: str) -> dict:
        raise NotImplementedError

    def launch(self, agent: str, name: str, prompt: str, worktree: Path | None,
               headless: bool, cfg: dict) -> dict:
        """拉起 worker；返回合并进 dispatch 记录的信息（worker_handle/worktree/branch 等）。

        worktree 为 None 时由 transport 自建（Orca 模式）；否则使用已建好的隔离树（hcom 模式）。"""
        raise NotImplementedError

    def wait_message(self, timeout: int) -> dict:
        """阻塞等一条 [FOREMAN:*] 消息，归一化为 {id, instance, data:{text,from}}；超时返回 {'timed_out': True}。"""
        raise NotImplementedError

    def doctor_checks(self, repo: "Repo", cfg: dict) -> list[dict]:
        return []

    def cleanup(self, repo: "Repo", dispatch: dict) -> list[str]:
        return []


_CONTEXT_CACHE: dict[str, dict] = {}


def detect_orca_context(repo: "Repo", cfg: dict) -> dict:
    """调用时判断本进程的运行时环境：是否跑在 Orca 管辖的终端里、是不是 worker。

    `ORCA_TERMINAL_HANDLE` 由 Orca 按终端注入，所以它既是"在 Orca 里"的证据，也是"我挂在哪块屏"。
    仅凭它有被继承的可能（同终端的子进程理应继承），因此再用 daemon 复核一次：终端仍存活吗、
    我是不是某个 live dispatch 的终端。"""
    handle = (os.environ.get("ORCA_TERMINAL_HANDLE") or "").strip()
    ctx = {"in_orca": bool(handle), "handle": handle or None,
           "terminal_live": False, "run_id": None, "worker_dispatch": None}
    if not handle:
        return ctx
    key = str(repo.root)
    if key in _CONTEXT_CACHE:
        return _CONTEXT_CACHE[key]
    o = Orca(repo, cfg)
    if not o.available():
        return ctx
    ctx["terminal_live"] = o._run("terminal", "show", "--terminal", handle,
                                 "--json", timeout=DETECT_TIMEOUT).returncode == 0
    if not ctx["terminal_live"]:
        _CONTEXT_CACHE[key] = ctx  # 句柄已死：不必再问 Run/worker（省两次往返）
        return ctx
    ctx["run_id"] = o.run_id(timeout=DETECT_TIMEOUT)
    for w in o.workers(timeout=DETECT_TIMEOUT):
        if w.get("terminal") == handle and w.get("liveness") == "live":
            ctx["worker_dispatch"] = w.get("dispatchId")
            break
    _CONTEXT_CACHE[key] = ctx
    return ctx


def assert_coordinator(repo: "Repo", cfg: dict) -> dict:
    """控制面命令只能由主管执行：worker 会话（Orca 里某个 live dispatch 的终端）一律拒绝。"""
    ctx = detect_orca_context(repo, cfg)
    if ctx["worker_dispatch"]:
        fail(f"本会话是 Orca worker（dispatch {ctx['worker_dispatch']}）：控制面命令只能在主管会话执行；"
             f"worker 按派工单实现并上报即可")
    return ctx


_TRANSPORT_CACHE: dict[str, tuple[str, str]] = {}


def resolve_transport(repo: "Repo", cfg: dict) -> tuple[str, str]:
    """返回 (transport, 来源)。explicit 配置（hcom/orca）优先；否则按运行时自动判定。"""
    raw = str(cfg.get("transport") or "auto").strip().lower()
    if raw in ("hcom", "orca"):
        return raw, "config 显式"
    key = str(repo.root)
    if key not in _TRANSPORT_CACHE:
        ctx = detect_orca_context(repo, cfg)
        if ctx["in_orca"] and ctx["terminal_live"]:
            _TRANSPORT_CACHE[key] = ("orca", "自动：Orca 终端")
        else:
            _TRANSPORT_CACHE[key] = ("hcom", "自动：非 Orca 终端")
    return _TRANSPORT_CACHE[key]


def effective_config(repo: "Repo", cfg: dict | None = None) -> dict:
    """把解析出的 transport 写进内存 cfg（不落盘），下游的 `cfg["transport"] == "orca"`
    与 worker_prompt / brief_text 无需改动。"""
    cfg = dict(cfg if cfg is not None else repo.config())
    name, source = resolve_transport(repo, cfg)
    cfg["transport"] = name
    cfg["transport_source"] = source
    return cfg


def make_transport(repo: "Repo", cfg: dict) -> Transport:
    if cfg.get("transport") == "orca":
        return Orca(repo, cfg)
    return Hcom(repo, cfg)


class Hcom(Transport):
    """hcom 实现。契约见决策记录 2026-11-12-hcom-event-layer-worktree-file-state.md 附录。"""

    def __init__(self, repo: Repo, cfg: dict):
        self.repo = repo
        self.bin = cfg.get("hcom_bin", "hcom")
        self.foreman_name = cfg["foreman_name"]

    def _run(self, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
        return run_cmd([self.bin, *args], cwd=self.repo.root, timeout=timeout)

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def self_identity(self) -> dict | None:
        """会话身份：hcom 托管启动的会话用 list self；ad-hoc（hcom start）会话用显式 --name 查询。"""
        proc = self._run("list", "self", "--json")
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                return None
        proc = self._run("list", self.foreman_name, "--json")
        if proc.returncode != 0:
            return None
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None

    def send(self, target: str, text: str) -> dict:
        proc = self._run(
            "send", f"@{target}", "--intent", "request", "--name", self.foreman_name, "--json", "--", text
        )
        if proc.returncode != 0:
            fail(f"hcom send 到 @{target} 失败：{(proc.stderr or proc.stdout).strip()}")
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except ValueError:
            fail(f"hcom send 回执不是 JSON：{proc.stdout.strip()[:200]}")

    def launch(self, agent: str, name: str, prompt: str, worktree: Path | None,
               headless: bool, cfg: dict) -> dict:
        args = [
            self.bin, agent,
            "--name", name,
            "--tag", cfg["tag"],
            "--dir", str(worktree),
            "--hcom-prompt", prompt,
            "--json",
        ]
        if headless:
            args.append("--headless")
        proc = run_cmd(args, cwd=self.repo.root, timeout=300)
        if proc.returncode != 0:
            fail(f"hcom 启动 worker 失败：{(proc.stderr or proc.stdout).strip()}")
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except ValueError:
            fail(f"hcom launch 回执不是 JSON：{proc.stdout.strip()[:200]}")
        if data.get("failed"):
            fail(f"hcom 启动 worker 失败：{data.get('errors')}")
        if not data.get("names"):
            fail("hcom 启动 worker 未返回实例名")
        return {"worker_handle": data["names"][0], "batch_id": data.get("batch_id")}

    def wait_message(self, timeout: int) -> dict:
        """hcom events --wait（hcom 自己的阻塞参数）命中输出单行事件 JSON（instance=发送者）；
        stdout 可能先出现 <hcom> 预览行，取最后一行 JSON。"""
        proc = self._run(
            "events", "--wait", str(timeout), "--type", "message", "--name", self.foreman_name,
            "--mention", self.foreman_name, timeout=timeout + 60,
        )
        for line in reversed((proc.stdout or "").strip().splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                return json.loads(line)
            except ValueError:
                continue
        return {"timed_out": True}

    def doctor_checks(self, repo: Repo, cfg: dict) -> list[dict]:
        checks: list[dict] = []
        hcom_bin = shutil.which(self.bin)
        checks.append({"name": "hcom", "ok": hcom_bin is not None,
                       "detail": f"hcom 不在 PATH（{self.bin}）" if hcom_bin is None else f"hcom={hcom_bin}",
                       "fix": "uv tool install hcom（不自动安装）" if hcom_bin is None else ""})
        if hcom_bin:
            ident = self.self_identity()
            checks.append({"name": "hcom-hosted", "ok": ident is not None,
                           "detail": "当前会话未接入 hcom（主管必须由 hcom 启动）" if ident is None
                                     else f"本会话身份：{ident.get('name')}",
                           "fix": "退出后用 hcom codex / hcom pi / hcom start 重启会话" if ident is None else ""})
        return checks


class Orca(Transport):
    """Orca 实现。契约见决策记录 2026-11-13-transport-abstraction-orca-adapter.md。

    launch = task-create + worker-start（Orca 托管 worktree）；
    消息 = orchestration send/check（首行协议头作 subject）；
    T3 = 原生 gate-create/gate-resolve；清理 = worktree rm。
    """

    def __init__(self, repo: Repo, cfg: dict):
        self.repo = repo
        self.bin = resolve_orca_bin()
        self.foreman_name = cfg["foreman_name"]
        self._run_id: str | None = None
        self._run_checked = False

    def _run(self, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
        return run_cmd([self.bin, *args], cwd=self.repo.root, timeout=timeout)

    def _ok_json(self, proc: subprocess.CompletedProcess, what: str) -> dict:
        if proc.returncode != 0:
            fail(f"{what} 失败（exit {proc.returncode}）：{(proc.stderr or proc.stdout).strip()[:300]}")
        raw = (proc.stdout or "").strip()
        data = None
        for candidate in (raw, raw.splitlines()[-1] if raw else ""):
            try:
                data = json.loads(candidate)
                break
            except (ValueError, IndexError):
                continue
        if not isinstance(data, dict):
            fail(f"{what} 回执不是 JSON：{raw[:200]}")
        if data.get("ok") is False:
            fail(f"{what} 失败：{json.dumps(data.get('error') or data, ensure_ascii=False)[:300]}")
        return data

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def run_id(self, timeout: int = 60) -> str | None:
        if not self._run_checked:
            proc = self._run("orchestration", "run-current", "--json", timeout=timeout)
            if proc.returncode == 0:
                try:
                    self._run_id = json.loads((proc.stdout or "").strip().splitlines()[-1])["result"]["runId"]
                except (ValueError, KeyError, IndexError):
                    self._run_id = None
            self._run_checked = True
        return self._run_id

    def self_identity(self) -> dict | None:
        rid = self.run_id()
        return {"name": f"run:{rid}"} if rid else None

    def repo_selector(self) -> str:
        proc = self._run("repo", "list", "--json", timeout=120)
        data = self._ok_json(proc, "orca repo list")
        items = data.get("result") or data
        if isinstance(items, dict):
            items = items.get("repos") or items.get("items") or []
        for it in items or []:
            path = it.get("path") or it.get("absPath") or ""
            if path and Path(path).resolve() == self.repo.root:
                rid = it.get("id") or it.get("repoId")
                if rid:
                    return f"id:{rid}"
        fail(f"仓库 {self.repo.root} 未注册到 Orca；先运行 orca repo add --path {self.repo.root}")

    def send(self, target: str, text: str) -> dict:
        lines = text.split("\n", 1)
        subject = lines[0]
        body = lines[1] if len(lines) > 1 else ""
        proc = self._run("orchestration", "send", "--to", target, "--subject", subject,
                         "--body", body, "--json", timeout=300)
        return self._ok_json(proc, f"orchestration send → {target}")

    def launch(self, agent: str, name: str, prompt: str, worktree: Path | None,
               headless: bool, cfg: dict) -> dict:
        run_id = self.run_id()
        if not run_id:
            fail("Orca 模式需要先绑定编排 Run：orca orchestration run-create --objective \"<目标>\" --json")
        sel = self.repo_selector()
        proc = self._run("orchestration", "task-create", "--task-title", name,
                         "--spec", prompt, "--json", timeout=300)
        tdata = self._ok_json(proc, "orchestration task-create")
        task_id = self._find_id(tdata)
        if not task_id:
            fail(f"task-create 回执中未找到任务 id：{json.dumps(tdata, ensure_ascii=False)[:300]}")
        args = ["orchestration", "worker-start", "--task", task_id, "--agent", agent,
                "--worktree", "new-top-level", "--repo", sel,
                "--base-branch", cfg["base"], "--name", name, "--json"]
        proc = self._run(*args, timeout=600)
        if proc.returncode != 0:
            detail = ((proc.stderr or "") + (proc.stdout or "")).strip()
            if "agent-hooks-review-prompt" in detail:
                fail("worker 启动被 agent hooks 信任菜单阻塞（agent_readiness）："
                     "在 Orca worker 终端选择 'Trust all and continue' 后重新 dispatch；"
                     "信任一次后后续 worker 不再询问")
            probe = self._run("orchestration", "worker-list", "--json", timeout=120)
            live = "live" in (probe.stdout or "") if probe.returncode == 0 else False
            hint = ("；注意：worker-list 显示有 live worker，可能已在终端内启动，"
                    "检查其终端后再决定重派或接手") if live else ""
            fail(f"orchestration worker-start 失败：{detail[:300]}{hint}")
        wdata = self._ok_json(proc, "orchestration worker-start")
        dispatch_id, wt_path, wt_sel, branch = self._find_worker(wdata)
        return {
            "worker_handle": f"dispatch:{dispatch_id}",
            "orchestration_task": task_id,
            "worktree": wt_path,
            "worktree_selector": wt_sel,
            "branch": branch,
        }

    def _find_id(self, data: dict) -> str | None:
        result = data.get("result") or data
        for key in ("taskId", "task_id", "id"):
            if isinstance(result, dict) and result.get(key):
                return str(result[key])
        return None

    def _find_worker(self, data: dict) -> tuple[str, str, str, str]:
        """从 worker-start 回执提取 (dispatchId, worktree 路径, selector, 分支名)。

        真实回执形状：workspace = "<repoId>::<worktreePath>"（即 worktree selector）。"""
        result = data.get("result") or data
        blob = json.dumps(result, ensure_ascii=False)
        dispatch_id = None
        for key in ("dispatchId", "dispatch_id", "id"):
            if isinstance(result, dict) and result.get(key):
                dispatch_id = str(result[key])
                break
        wt_path = wt_sel = branch = ""
        m = re.search(r'"workspace"\s*:\s*"([^"]+)"', blob)
        if m:
            wt_sel = m.group(1)
            if "::" in wt_sel:
                wt_path = wt_sel.split("::", 1)[1]
        if not wt_path:
            m = re.search(r'"(?:worktreePath|path)"\s*:\s*"([^"]+)"', blob)
            if m:
                wt_path = m.group(1)
                wt_sel = f"path:{wt_path}"
        m = re.search(r'"(?:branch|branchName)"\s*:\s*"([^"]+)"', blob)
        if m:
            branch = m.group(1)
        if not dispatch_id or not wt_path:
            fail(f"worker-start 回执缺少 dispatchId/worktree 信息：{blob[:400]}")
        return dispatch_id, wt_path, wt_sel, branch

    def wait_message(self, timeout: int) -> dict:
        """check --wait：JSON 在 stdout，keepalive 在 stderr（不干扰解析）。

        批次里非协议消息（heartbeat/status/escalation）会被 check 标为已处理，
        因此落一行日志保持可追溯，不静默丢弃。
        """
        proc = self._run("orchestration", "check", "--wait", "--timeout-ms", str(timeout * 1000),
                         "--json", timeout=timeout + 60)
        try:
            data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"timed_out": True}
        msgs = self._extract_messages(data)

        def as_text(msg: dict) -> str:
            return "\n".join(x for x in (msg.get("subject"), msg.get("body")) if x)

        for i, msg in enumerate(msgs):
            text = as_text(msg)
            if header_candidates(text):
                # check 是消费式的：本批其余消息不会再出现，必须留档，不能静默丢
                rest = msgs[:i] + msgs[i + 1:]
                extra = []
                if rest:
                    self._log_unhandled(rest)
                    extra = [m.get("type") for m in rest]
                return {"id": msg.get("id") or msg.get("messageId"),
                        "instance": msg.get("from") or msg.get("sender") or "?",
                        "data": {"text": text, "from": msg.get("from") or "?"},
                        **({"batch_extra": extra} if extra else {})}
        if msgs:
            self._log_skipped(msgs)
            return {"timed_out": True,
                    "skipped": [{"type": m.get("type"), "from": m.get("from"),
                                 "subject": (m.get("subject") or "")[:120],
                                 "payload": m.get("payload")} for m in msgs]}
        return {"timed_out": True}

    def _log_skipped(self, msgs: list[dict]) -> None:
        lines = []
        for m in msgs:
            lines.append(f"{now_iso()}\t{m.get('id') or m.get('messageId')}\t"
                         f"{m.get('type')}\t{m.get('from')}\t{(m.get('subject') or '')[:80]}")
        append_log(self.repo.logs_dir / "transport-skipped.log", "\n".join(lines))

    def _log_unhandled(self, msgs: list[dict]) -> None:
        """同批次里未被处理的消息（含协议消息）全文落盘，避免消费式读取丢数据。"""
        lines = [f"=== {now_iso()} {m.get('id') or m.get('messageId')} type={m.get('type')} "
                 f"from={m.get('from')}\n{m.get('subject') or ''}\n{m.get('body') or ''}"
                 for m in msgs]
        append_log(self.repo.logs_dir / "batch-unhandled.log", "\n".join(lines))

    def _extract_messages(self, data: dict) -> list[dict]:
        result = data.get("result") or data
        for key in ("messages", "items", "batch"):
            if isinstance(result, dict) and isinstance(result.get(key), list):
                return [m for m in result[key] if isinstance(m, dict)]
        if isinstance(result, list):
            return [m for m in result if isinstance(m, dict)]
        return []

    def workers(self, timeout: int = 120) -> list[dict]:
        """当前 Run 的 dispatch 列表（worker-list）；失败返回空表（检测用途，不 fail）。"""
        proc = self._run("orchestration", "worker-list", "--json", timeout=timeout)
        if proc.returncode != 0:
            return []
        try:
            data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return []
        res = data.get("result") or data
        rows = res.get("workers") if isinstance(res, dict) else None
        return [w for w in rows or [] if isinstance(w, dict)]

    def gate_create(self, orchestration_task: str, question: str, options: list[str]) -> str:
        args = ["orchestration", "gate-create", "--task", orchestration_task,
                "--question", question, "--json"]
        if options:
            args += ["--options", json.dumps(options, ensure_ascii=False)]
        data = self._ok_json(self._run(*args, timeout=300), "orchestration gate-create")
        gate_id = self._find_id(data)
        if not gate_id:
            fail(f"gate-create 回执未找到 gate id：{json.dumps(data, ensure_ascii=False)[:300]}")
        return gate_id

    def gate_resolve(self, gate_id: str, resolution: str) -> dict:
        return self._ok_json(self._run("orchestration", "gate-resolve", "--id", gate_id,
                                       "--resolution", resolution, "--json", timeout=300),
                             "orchestration gate-resolve")

    def doctor_checks(self, repo: Repo, cfg: dict) -> list[dict]:
        checks: list[dict] = []
        checks.append({"name": "orca-cli", "ok": self.available(),
                       "detail": f"orca CLI 不可用（{self.bin}）" if not self.available() else f"orca={self.bin}",
                       "fix": "安装 Orca 或设置 ORCA_CLI_COMMAND" if not self.available() else ""})
        if self.available():
            # daemon 探活用 repo list：不依赖当前目录是否已注册 worktree
            proc = self._run("repo", "list", "--json", timeout=120)
            reachable = proc.returncode == 0
            repos_stdout = proc.stdout or ""
            checks.append({"name": "orca-daemon", "ok": reachable,
                           "detail": "Orca daemon 不可达" if not reachable else "daemon 可达",
                           "fix": "启动 Orca（orca open）" if not reachable else ""})
            rid = self.run_id()
            checks.append({"name": "orca-run", "ok": rid is not None,
                           "detail": "当前会话未绑定编排 Run" if rid is None else f"Run: {rid}",
                           "fix": f"orca orchestration run-create --objective \"<目标>\" --json" if rid is None else ""})
            has_identity = bool(os.environ.get("ORCA_TERMINAL_HANDLE"))
            checks.append({"name": "orca-identity", "ok": has_identity,
                           "detail": "缺少 ORCA_TERMINAL_HANDLE：check/send 会报 no_active_sender_terminal，主管收不到信也无法回话"
                                     if not has_identity else "本会话有编排身份（可收发消息）",
                           "fix": "在 Orca 管辖的终端里启动主管会话" if not has_identity else ""})
            if rid is not None:
                registered = reachable and str(repo.root) in repos_stdout
                checks.append({"name": "orca-repo", "ok": registered,
                               "detail": f"仓库未注册到 Orca" if not registered else "仓库已注册",
                               "fix": f"orca repo add --path {repo.root}" if not registered else ""})
        return checks

    def cleanup(self, repo: Repo, dispatch: dict) -> list[str]:
        done = []
        handle = str(dispatch.get("worker_handle") or "")
        if handle.startswith("dispatch:"):
            did = handle.split(":", 1)[1]
            proc = self._run("orchestration", "worker-release", "--dispatch", did, "--json", timeout=300)
            done.append(f"worker {did} 已 release" if proc.returncode == 0 else
                        f"worker {did} release 未成功（可能仍活；用 orca orchestration worker-stop 或 terminal close 兜底）")
        orch_task = dispatch.get("orchestration_task")
        if orch_task:
            proc = self._run("orchestration", "task-update", "--id", orch_task, "--status", "completed",
                             "--result", f"foreman {dispatch.get('id')} 已合并并清理", "--json", timeout=300)
            done.append(f"orchestration task {orch_task} 已结算 completed" if proc.returncode == 0 else
                        f"orchestration task {orch_task} 结算失败（orca orchestration task-list 查看）")
        sel = dispatch.get("worktree_selector")
        if sel:
            self._ok_json(self._run("worktree", "rm", "--worktree", sel, "--force", "--delete-branch",
                                    "--json", timeout=300), "orchestration worktree rm")
            done.append(f"Orca worktree {sel} 已删（含分支）")
        return done


# ---------------------------------------------------------------- 消息与派工单


def worker_prompt(cfg: dict, task: dict, dispatch: dict, brief_path: Path) -> str:
    foreman = cfg["foreman_name"]
    head = (
        f"你是 vibe-foreman 的 worker，负责任务 {task['id']}（派工记录 {dispatch['id']}）。\n"
        f"1. 阅读并完整遵循仓库 AGENTS.md（若存在）与派工单 {brief_path}（注意：派工单在主仓库目录，不在本 worktree 内）。\n"
        "2. 只做派工单范围内的事；完成后在当前 worktree 提交，每个提交对应一件事，提交信息说明为什么。\n"
    )
    if cfg.get("transport") == "orca":
        run_id = dispatch.get("run_id") or "<run_id>"
        proto = (
            f"3. 需要裁决时运行：orca orchestration send --to run:{run_id} --subject "
            f"'[FOREMAN:ASK] task={task['id']} dispatch={dispatch['id']}' "
            f"--body '<问题+选项+推荐项>'，然后结束回合等回复。\n"
            f"4. 全部完成并自测通过后运行：orca orchestration send --to run:{run_id} --type worker_done "
            f"--outcome succeeded --subject '[FOREMAN:DONE] task={task['id']} dispatch={dispatch['id']}' "
            f"--body '<摘要+改动文件+验证命令与输出+分支提交>'，然后停止。"
            f"（放弃任务时用 --outcome failed。若 Orca 回 'worker_done requires taskId'，"
            f"用 `orca orchestration worker-list --json` 查本次 dispatch 的 taskId，"
            f"再补 --task-id <taskId> 重发同一封，不要改正文。）\n"
            f"5. 卡住时发 --subject '[FOREMAN:ESCALATE] task={task['id']} dispatch={dispatch['id']}' "
            f"--body '<已尝试方案+阻塞点>'，然后停止。\n"
            "Orca 的协议头放在 --subject，只在协议头里写 task/dispatch，正文放 --body。\n"
            "回复会以 Orca 唤醒到达；必要时先 orca orchestration check --json 读取。\n"
        )
    else:
        proto = (
            f"3. 需要裁决时运行：hcom send @{foreman} --intent request --name <你的实例名> -- "
            f"'[FOREMAN:ASK] task={task['id']} dispatch={dispatch['id']} <问题+选项+推荐项>'，"
            f"然后结束回合等待回复。\n"
            f"4. 全部完成并自测通过后运行：hcom send @{foreman} --intent request --name <你的实例名> -- "
            f"'[FOREMAN:DONE] task={task['id']} dispatch={dispatch['id']} <摘要+改动文件+验证命令与输出+分支提交>'，"
            f"然后停止。\n"
            f"5. 卡住时发 '[FOREMAN:ESCALATE] task={task['id']} dispatch={dispatch['id']} <已尝试方案+阻塞点>'，"
            f"然后停止。\n"
            "<你的实例名> 用你启动时 [HCOM SESSION] bootstrap 里告知的名字；不要用本段文字里的任何名字。\n"
        )
    return head + proto + ("红线：不 merge、不 push、不删分支、不跨范围改动、不提交密钥；"
                           "不写 docs/memory/ 与 docs/progress/（主管独占）。"
                           "消息一律走所选编排通道，首行必须带上面的协议头（Orca 放 --subject，hcom 放正文首行）。")


def brief_text(cfg: dict, task: dict) -> str:
    scope = "\n".join(f"- {s}" for s in task.get("scope", [])) or "- 全库（谨慎）"
    orca = cfg.get("transport") == "orca"
    dispatches = task.get("dispatches") or []
    run_id = (dispatches[-1].get("run_id") if dispatches else None) or "<run_id>"
    where = (f"上报目标：Orca run:{run_id}（协议头放 --subject，正文放 --body）" if orca
             else f"上报目标：hcom @{cfg['foreman_name']}（协议头放消息正文首行）")
    proto_head = ("`--subject` 必须是 `[FOREMAN:<TYPE>] task={tid} dispatch=<dispatch-id>`"
                  if orca else
                  "消息正文首行必须是 `[FOREMAN:<TYPE>] task={tid} dispatch=<dispatch-id>`")
    return f"""# 派工单 {task['id']}：{task['title']}

- 派工时间：{now_iso()}
- worker agent：{task.get('agent') or "（dispatch 时按主管会话决定，未定则用 config 兜底）"}
- {where}

## 目标与背景

{task.get('goal', task['title'])}
{f"规格文档：{task['spec']}" if task.get("spec") else ""}

## 范围（只允许改动以下路径）

{scope}

{f"## 补充说明\\n\\n{task['notes']}\\n" if task.get("notes") else ""}## 完成定义（验收命令，完成后必须在 worktree 内真实跑过）

```bash
{task.get('acceptance') or '（主管尚未定义；开工前先通过 ASK 索要）'}
```

## 协议

{proto_head.format(tid=task['id'])}，
TYPE 取 ASK / DONE / ESCALATE。请示用 ASK（附问题、选项、推荐项），等 ANSWER；
完成用 DONE（附摘要、改动文件、验证命令与输出、分支/提交）；卡住用 ESCALATE。

## 红线

- 只改范围内文件；不 merge、不 push、不删分支、不跨范围改动。
- 不提交密钥；不跳过测试；遵循仓库 AGENTS.md 与 Ask-first 清单。
- 不写 `docs/memory/`、不写 `docs/progress/`：会话态与竣工单由主管独占写入；你的结论写进 DONE 正文。
- 发现影响面超出本单范围 → 发 ASK 附影响面清单，不许自行越界。
"""


# ---------------------------------------------------------------- 命令：doctor


def cmd_doctor(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str, fix: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    git_ok = shutil.which("git") is not None
    check("git", git_ok, "PATH 中未找到 git" if not git_ok else "git 可用",
          "安装 git" if not git_ok else "")
    is_repo = git_ok and repo.is_git_repo()
    check("git-repo", is_repo, f"{repo.root} 不是 git 仓库" if not is_repo else f"{repo.root}",
          "在 git 仓库根目录运行" if not is_repo else "")
    cfg = repo.load_or_default_config()
    if is_repo:
        branch = repo.current_branch()
        base_ok = repo.git_try("rev-parse", "--verify", cfg["base"]).returncode == 0
        check("base-branch", base_ok, f"base 分支 {cfg['base']} 不存在" if not base_ok else f"base={cfg['base']}",
              f"init --base <分支> 修正（当前分支 {branch}）" if not base_ok else "")
        clean = repo.is_clean()
        check("worktree-clean", clean, "工作区有未提交改动" if not clean else "工作区干净",
              "先提交或 stash" if not clean else "")
        ignored = repo.git_try("check-ignore", "-q", ".vibe").returncode == 0
        check("state-ignored", ignored,
              ".vibe 未被忽略（init 会写入 .git/info/exclude）" if not ignored else ".vibe 已忽略",
              "运行 init" if not ignored else "")
        cfg_docs = docs_paths(cfg)
        progress_ok = (repo.root / cfg_docs["progress"]).is_dir()
        check("docs-progress", progress_ok,
              f"{cfg_docs['progress']} 不存在（竣工单无落点，先跑 vibe-base 初始化）" if not progress_ok
              else cfg_docs["progress"],
              "用 vibe-base 技能初始化项目骨架" if not progress_ok else "")

    transport_name, transport_source = resolve_transport(repo, cfg)
    cfg = dict(cfg, transport=transport_name)
    transport = make_transport(repo, cfg)
    for c in transport.doctor_checks(repo, cfg):
        check(c["name"], c["ok"], c["detail"], c["fix"])
    ctx = detect_orca_context(repo, cfg)
    check("runtime", True,
          ("Orca 终端（worker "
           f"{ctx['worker_dispatch']}，不能跑控制面）") if ctx["worker_dispatch"]
          else ("Orca 终端（主管会话）" if ctx["in_orca"] and ctx["terminal_live"]
                else ("Orca 终端句柄失效" if ctx["in_orca"] else "非 Orca 托管终端（走 hcom 通道）")),
          "")
    detected = detect_session_agent()
    check("session-agent", True,
          f"主管会话识别为 {detected}（worker 默认用它）" if detected
          else "未识别主管会话 agent（/proc 与环境标记都没命中）→ worker 用 config 兜底",
          "")
    for agent in ("codex", "pi"):
        check(f"agent:{agent}", shutil.which(agent) is not None,
              f"{agent} 不在 PATH" if shutil.which(agent) is None else f"{agent} 可用",
              f"安装 {agent} 或 dispatch --agent 显式指定" if shutil.which(agent) is None else "")

    ok = all(c["ok"] for c in checks)
    emit(args, {"ok": ok, "transport": transport_name, "transport_source": transport_source,
                "checks": checks},
          f"transport：{transport_name}（{transport_source}）\n"
          + "\n".join(f"[{'OK ' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}"
                      + (f"（{c['fix']}）" if c["fix"] else "") for c in checks)
          + f"\n结果：{'就绪' if ok else '存在问题，按提示处理后重试'}")
    return EXIT_OK if ok else EXIT_ERROR


# ---------------------------------------------------------------- 命令：init


def cmd_init(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    if not repo.is_git_repo():
        fail(f"{repo.root} 不是 git 仓库")
    if (repo.root / ".foreman").exists() and not repo.foreman.exists():
        fail("检测到旧版 .foreman/：本版本过程态改为 .vibe/；确认无在跑任务后 `mv .foreman .vibe` 再重试")
    for d in (repo.foreman, repo.tasks_dir, repo.worktrees_dir, repo.archive_dir, repo.logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    before = read_json(repo.config_path) if repo.config_path.exists() else None
    cfg = repo.load_or_default_config()
    if args.base:
        cfg["base"] = args.base
    if args.agent:
        cfg["default_agent"] = args.agent
    if args.transport:
        if args.transport not in ("hcom", "orca", "auto"):
            fail("--transport 只支持 hcom / orca / auto")
        cfg["transport"] = args.transport
    if args.foreman_name:
        cfg["foreman_name"] = args.foreman_name
    if cfg != before:
        write_json(repo.config_path, cfg)

    exclude = repo.root / ".git" / "info" / "exclude"
    lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    # .vibe 必须忽略；BUILD_NOISE 是 merge 复跑验收会在主仓库留下的产物，
    # 不忽略会让下一个任务的 merge 被「工作区不干净」挡住（E2E 实测）。
    added = [x for x in [".vibe", *BUILD_NOISE] if x not in lines]
    if added:
        write_text(exclude, "\n".join([*lines, *added]) + "\n")

    ignored = repo.git_try("check-ignore", "-q", ".vibe").returncode == 0
    if not ignored:
        fail("写入 .git/info/exclude 后 .vibe 仍未被忽略，请手工检查")
    shown = cfg.get("transport") or "auto（在 Orca 终端→orca，否则 hcom）"
    emit(args, {"ok": True, "config": cfg, "base": cfg["base"],
                "transport": cfg.get("transport") or "auto"},
         f"init 完成：base={cfg['base']}，transport={shown}，默认 worker agent={cfg['default_agent']}"
         f"（session 优先，见 docs/governance/orchestration.md），.vibe/ 已忽略（幂等，未覆盖已有配置）\n"
         f"docs 落点：progress={docs_paths(cfg)['progress']} decisions={docs_paths(cfg)['decisions']}")
    return EXIT_OK


# ---------------------------------------------------------------- 命令：task


def cmd_task_new(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    assert_coordinator(repo, effective_config(repo))
    if not repo.config_path.exists():
        fail(".vibe 未初始化，先运行 init")
    cfg = effective_config(repo)
    task_id = repo.next_task_id()
    task = {
        "id": task_id,
        "title": args.title,
        "slug": slugify(args.title),
        "spec": args.spec,
        "agent": args.agent,  # None = dispatch 时按主管会话决定
        "scope": parse_scopes(args.scope),
        "acceptance": args.accept,
        "notes": args.notes,
        "goal": args.goal or args.title,
        "docs": {"progress": None, "decisions": []},
        "status": "ready" if args.accept else "pending",
        "attempts": 0,
        "dispatches": [],
        "asks": [],
        "gates": [],
        "decisions": [],
        "review": None,
        "merge": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "log": [{"at": now_iso(), "event": "created", "detail": f"task new：{args.title}"}],
    }
    tdir = repo.task_dir(task_id)
    task["docs"]["progress"] = progress_rel_for(cfg, task)
    write_json(tdir / "task.json", task)
    write_text(tdir / "brief.md", brief_text(cfg, task))
    emit(args, {"id": task_id, "status": task["status"], "dir": str(tdir)},
         f"已创建 {task_id}（{task['status']}）：{tdir}\n{'' if args.accept else '注意：未定义验收命令，dispatch 前用 task set-acceptance 补上'}")
    return EXIT_OK


def cmd_task_set_acceptance(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    assert_coordinator(repo, effective_config(repo))
    task = repo.load_task(args.task_id)
    task["acceptance"] = args.accept
    if task["status"] == "pending":
        repo.set_status(task, "ready")
        repo.log_task(task, "ready", f"定义验收命令：{args.accept}")
    repo.save_task(task)
    write_text(repo.find_task_dir(task["id"]) / "brief.md", brief_text(repo.config(), task))
    emit(args, {"id": task["id"], "status": task["status"], "acceptance": args.accept},
         f"{task['id']} 验收命令已设为：{args.accept}（状态 {task['status']}）")
    return EXIT_OK


def cmd_task_abandon(args: argparse.Namespace) -> int:
    """放弃当前派工（worker 死亡/不可恢复），置 failed 以便重派或交人类。"""
    repo = Repo(Path(args.repo))
    assert_coordinator(repo, effective_config(repo))
    task = repo.load_task(args.task_id)
    if task["status"] not in {"dispatched", "running", "waiting_answer", "waiting_human", "review"}:
        fail(f"任务 {task['id']} 状态为 {task['status']}，只有 worker 活动阶段可 abandon")
    if task["attempts"] >= repo.config().get("max_attempts", 3):
        repo.set_status(task, "blocked")
    else:
        repo.set_status(task, "failed")
    repo.log_task(task, task["status"], f"主管放弃本派工：{args.reason}")
    repo.save_task(task)
    emit(args, {"id": task["id"], "status": task["status"], "reason": args.reason},
         f"{task['id']} 已放弃本派工（{args.reason}）→ {task['status']}；"
         + ("尝试次数已用尽，交人类解锁后再 dispatch" if task["status"] == "blocked"
            else f"可 dispatch 重派（尝试 {task['attempts']}/{repo.config().get('max_attempts', 3)}）"))
    return EXIT_OK


def cmd_task_set_scope(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    assert_coordinator(repo, effective_config(repo))
    task = repo.load_task(args.task_id)
    scopes = parse_scopes(args.scope)
    if not scopes:
        fail("--scope 至少给一个路径")
    if task["status"] == "completed":
        fail(f"任务 {task['id']} 已合并归档，不能改范围")
    if task["status"] == "merge_ready":
        fail(f"任务 {task['id']} 已审查待合并，此时改范围会让 review 证据失效；"
             f"要么直接 merge，要么 task abandon 后重派（review 只在收到 DONE 后可跑）")
    task["scope"] = scopes
    if task["status"] == "pending" and task.get("acceptance"):
        repo.set_status(task, "ready")
    repo.log_task(task, task["status"], f"调整范围：{', '.join(scopes)}")
    repo.save_task(task)
    write_text(repo.find_task_dir(task["id"]) / "brief.md", brief_text(repo.config(), task))
    emit(args, {"id": task["id"], "status": task["status"], "scope": scopes},
         f"{task['id']} 范围已设为：{', '.join(scopes)}（状态 {task['status']}）")
    return EXIT_OK


def cmd_task_list(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    tasks = []
    for base in (repo.tasks_dir, repo.archive_dir):
        if not base.exists():
            continue
        for p in sorted(base.iterdir()):
            tj = p / "task.json"
            if tj.exists():
                t = read_json(tj)
                tasks.append({"id": t["id"], "title": t["title"], "status": t["status"],
                              "attempts": t["attempts"], "archived": base == repo.archive_dir})
    emit(args, {"tasks": tasks},
         "\n".join(f"{t['id']}  {t['status']:<14} attempts={t['attempts']}  {t['title']}"
                   + ("（已归档）" if t["archived"] else "") for t in tasks) or "（无任务）")
    return EXIT_OK


def cmd_task_show(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    task = repo.load_task(args.task_id)
    if args.json:
        print(json.dumps(task, ensure_ascii=False, indent=2))
    else:
        d = repo.find_task_dir(task["id"])
        dispatches = "\n".join(
            f"  {x['id']}  {x.get('worker_handle')}  attempts={x.get('attempt')}" for x in task["dispatches"])
        print(f"{task['id']} [{task['status']}] {task['title']}")
        print(f"目录：{d}\n验收：{task.get('acceptance')}\n派工：\n{dispatches or '  （无）'}")
        print("最近事件：\n" + "\n".join(f"  {x['at']} {x['event']}  {x['detail']}" for x in task["log"][-8:]))
    return EXIT_OK


# ---------------------------------------------------------------- 命令：dispatch


def cmd_dispatch(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    cfg = effective_config(repo)
    assert_coordinator(repo, cfg)
    task = repo.load_task(args.task_id)
    if task["status"] not in ("ready", "failed"):
        fail(f"任务 {task['id']} 状态为 {task['status']}，只允许 ready/failed 派工"
             + ("；failed 需先处理失败原因" if task["status"] == "failed" else ""))
    if not task.get("acceptance"):
        fail(f"任务 {task['id']} 未定义验收命令，先 task set-acceptance")
    max_attempts = cfg.get("max_attempts", 3)
    if task["attempts"] >= max_attempts:
        repo.set_status(task, "blocked")
        repo.log_task(task, "blocked", f"尝试 {task['attempts']}/{max_attempts} 次后仍失败，交人类处理")
        repo.save_task(task)
        fail(f"任务 {task['id']} 已尝试 {task['attempts']}/{max_attempts} 次，进入 blocked，不得继续自动重派")

    wt = repo.worktrees_dir / task["id"]
    branch = f"foreman/{task['id']}-{task['slug']}"
    is_retry = task["status"] == "failed"

    dispatch_id = repo.next_dispatch_id(task)
    worker_handle = f"w-{task['id']}"
    dispatch = {
        "id": dispatch_id, "attempt": task["attempts"] + 1, "at": now_iso(),
        "agent": None, "agent_source": None, "headless": bool(args.headless),
        "worktree": str(wt), "branch": branch, "worker_handle": worker_handle,
    }

    transport = make_transport(repo, cfg)
    if not transport.available():
        fail(f"编排通道 {cfg.get('transport', 'hcom')} 不可用；先处理环境再派工（任务保持 " + task["status"] + "）")
    ident = transport.self_identity()
    session_agent = (ident.get("tool") if isinstance(ident, dict) else None) or detect_session_agent()
    agent, agent_source = resolve_worker_agent(cfg, task, args.agent, session_agent)
    if cfg.get("transport") == "orca":
        run_id = transport.run_id()
        if not run_id:
            fail("Orca 模式需要先绑定编排 Run：orca orchestration run-create --objective \"<目标>\" --json")
        dispatch["run_id"] = run_id
    dispatch.update(agent=agent, agent_source=agent_source)
    prompt = worker_prompt(cfg, task, dispatch, repo.find_task_dir(task["id"]) / "brief.md")

    created_tree = False
    if cfg.get("transport") == "orca":
        # Orca 托管隔离：不手工建树，worker-start 自建托管 worktree
        tree_note = f"{dispatch_id} 由 Orca 创建托管 worktree（base={cfg['base']}）"
    else:
        # 建树：base 上开分支 + worktree；重派（failed）时复用既有树，不重建，不改状态
        branch_exists = repo.git_try("rev-parse", "--verify", branch).returncode == 0
        if wt.exists():
            if not is_retry or not branch_exists:
                fail(f"worktree 已存在（{wt}）但任务不是可重派状态：不重复建树；请人工检查后处理旧树")
            tree_note = f"{dispatch_id} 复用既有树 {branch} @ {wt}"
        else:
            if branch_exists:
                fail(f"分支 {branch} 已存在但 worktree 缺失：状态不一致，请人工处理")
            repo.git("branch", branch, cfg["base"])
            repo.git("worktree", "add", str(wt), branch)
            tree_note = f"{dispatch_id} 建树 {branch} @ {wt}"
            created_tree = True

    prev_status = task["status"]
    repo.set_status(task, "dispatched")
    repo.log_task(task, "dispatched", tree_note)
    try:
        info = transport.launch(agent, worker_handle, prompt,
                                None if cfg.get("transport") == "orca" else wt,
                                headless=bool(args.headless), cfg=cfg)
    except SystemExit:
        if created_tree:
            # 本次新建的空树无 worker 产出，失败即清理，避免 ready 态残留孤儿树
            repo.git("worktree", "remove", "--force", str(wt))
            repo.git("branch", "-D", branch)
            tree_note_extra = "，已清理本次新建的空树"
        else:
            tree_note_extra = ""
        task["status"] = prev_status
        repo.log_task(task, "dispatch-failed",
                      f"{dispatch_id} worker 启动失败，任务回到 {prev_status}{tree_note_extra}")
        repo.save_task(task)
        raise
    dispatch.update(info)
    task["dispatches"].append(dispatch)
    task["attempts"] = dispatch["attempt"]
    repo.set_status(task, "running")
    repo.log_task(task, "running", f"{dispatch_id} worker={dispatch['worker_handle']}（{agent}{' headless' if args.headless else ''}）")
    repo.save_task(task)

    write_text(repo.find_task_dir(task["id"]) / "brief.md", brief_text(cfg, task))
    emit(args, {"id": task["id"], "dispatch": dispatch_id, "worker": dispatch["worker_handle"],
                "branch": dispatch.get("branch"), "worktree": dispatch["worktree"], "status": task["status"]},
         f"{task['id']} {dispatch_id} 已派工：worker={dispatch['worker_handle']}（{agent}，来自{agent_source}），"
         f"分支 {dispatch.get('branch')}，worktree {dispatch['worktree']}\n主管现在结束回合，等唤醒（ASK/DONE/ESCALATE）。")
    return EXIT_OK


# ---------------------------------------------------------------- 命令：events / reply / ask


def save_evidence(repo: Repo, task_id: str, name: str, text: str) -> Path:
    path = repo.task_dir(task_id) / "evidence" / name
    write_text(path, text)
    return path


def apply_protocol_event(repo: Repo, cfg: dict, event: dict) -> dict:
    """把一条 hcom 消息事件按协议头落到任务状态机。"""
    data = event.get("data", {})
    text = data.get("text", "")
    sender = event.get("instance", data.get("from", "?"))
    cands = header_candidates(text)
    if not cands:
        raw = save_evidence(repo, "_unparsed",
                            f"unparsed-{now_iso().replace(':', '')}-{safe_name(sender)}.md", text)
        fail(f"消息缺协议头（来自 {sender}）：{text[:120]!r}；全文已留档 {raw}；"
             f"worker 必须用 [FOREMAN:<TYPE>] task=... dispatch=... 首行")
    if len(cands) > 1:
        raw = save_evidence(repo, "_unparsed",
                            f"ambiguous-{now_iso().replace(':', '')}-{safe_name(sender)}.md", text)
        fail(f"消息含 {len(cands)} 个不同协议头（来自 {sender}）：{cands}；全文已留档 {raw}；"
             f"一次只报一个 task/dispatch，引述他任务的消息要删掉协议头")
    parsed = cands[0]
    ptype, task_id, dispatch_id = parsed
    task = repo.load_task(task_id)
    dref = f"{dispatch_id}（来自 {sender}）"
    result: dict = {"event_id": event.get("id"), "type": ptype, "task": task_id,
                    "dispatch": dispatch_id, "from": sender}

    if ptype == "ASK":
        repo.set_status(task, "waiting_answer")
        entry = {"dispatch": dispatch_id, "from": sender, "at": now_iso(), "question": text}
        task.setdefault("asks", []).append(entry)
        save_evidence(repo, task_id, f"ask-{safe_name(dispatch_id)}.md", text)
        repo.log_task(task, "waiting_answer", dref)
        result["status"] = "waiting_answer"
        result["hint"] = "T1 直接答；T2 拍板后 reply；涉及 T3 先 ask 进等待人类"
    elif ptype == "DONE":
        repo.set_status(task, "review")
        save_evidence(repo, task_id, f"done-{safe_name(dispatch_id)}.md", text)
        repo.log_task(task, "review", f"{dref} 上报完成，待审查")
        result["status"] = "review"
        result["hint"] = "运行 review " + task_id
    elif ptype == "ESCALATE":
        repo.set_status(task, "failed")
        save_evidence(repo, task_id, f"escalate-{safe_name(dispatch_id)}.md", text)
        if task["attempts"] >= cfg.get("max_attempts", 3):
            repo.set_status(task, "blocked")
            repo.log_task(task, "blocked", f"{dref} 第 {task['attempts']} 次失败，交人类")
            result["status"] = "blocked"
        else:
            repo.log_task(task, "failed", dref)
            result["status"] = "failed"
            result["hint"] = "处理后可重新 dispatch（计入尝试次数）"
    elif ptype == "ANSWER":
        result["status"] = task["status"]
        result["hint"] = "主管不该收到 ANSWER；仅记录"
    else:
        result["status"] = task["status"]
        result["hint"] = "未触发状态迁移"

    repo.save_task(task)
    result["task_status"] = task["status"]
    return result


def cmd_events(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    cfg = effective_config(repo)
    assert_coordinator(repo, cfg)
    transport = make_transport(repo, cfg)
    if not transport.available():
        fail(f"编排通道 {cfg.get('transport', 'hcom')} 不可用")
    event = transport.wait_message(args.timeout)
    if event.get("timed_out"):
        skipped = event.get("skipped") or []
        hint = ""
        if skipped:
            kind_set = {str(x.get("type")) for x in skipped}
            kinds = ", ".join(sorted(kind_set))
            warn = "escalation" in kind_set
            hint = (f"\n本批 {len(skipped)} 条非协议消息（{kinds}）已记入 logs/transport-skipped.log"
                    + ("；escalation 说明 worker 结束回合未上报：用 worker-read 看它停在哪，"
                       "再 send --to dispatch:<orcaId> 给指引或 task abandon 后重派" if warn else ""))
        emit(args, {"timed_out": True, "skipped": skipped},
             "等待超时，无匹配协议消息" + hint)
        return EXIT_ERROR
    extra = event.get("batch_extra") or []
    result = apply_protocol_event(repo, cfg, event)
    if extra:
        result["batch_extra"] = extra
    emit(args, result,
         f"[{result['type']}] {result['task']}/{result['dispatch']} 来自 {result['from']} → 任务状态 {result['task_status']}"
         + (f"\n建议：{result['hint']}" if result.get("hint") else "")
         + (f"\n注意：同批还有 {len(result['batch_extra'])} 条消息未处理"
            f"（{', '.join(str(x) for x in result['batch_extra'])}），全文已留档 "
            f"{repo.logs_dir / 'batch-unhandled.log'}" if result.get("batch_extra") else ""))
    return EXIT_OK


def cmd_reply(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    cfg = effective_config(repo)
    assert_coordinator(repo, cfg)
    task = repo.load_task(args.task_id)
    if task["status"] not in WORKER_STATUSES:
        fail(f"任务 {task['id']} 状态为 {task['status']}，只在 waiting_answer/waiting_human 时 reply")
    dispatches = task["dispatches"]
    target = None
    if args.dispatch:
        target = next((d for d in dispatches if d["id"] == args.dispatch), None)
        if target is None:
            fail(f"派工记录 {args.dispatch} 不存在于 {task['id']}")
    else:
        target = dispatches[-1] if dispatches else None
    if target is None:
        fail(f"任务 {task['id']} 没有派工记录，无法回复")

    # Ask-first（原 T3）裁决必须同时落决策记录：过程态会消失，决策库不会（AGENTS.md §5）
    was_human_gate = task["status"] == "waiting_human"
    dec_docs = []
    for raw in args.decision_doc or []:
        p = Path(raw)
        if p.is_absolute() or ".." in p.parts:
            fail(f"决策记录必须是仓库内相对路径（不允许绝对路径或 ..）：{raw}")
        rel = p.as_posix()
        allowed = docs_paths(cfg)["decisions"].rstrip("/") + "/"
        if not rel.startswith(allowed):
            fail(f"决策记录必须落在 {allowed} 下：{rel}")
        dec_docs.append(rel)
    if was_human_gate:
        if not dec_docs:
            fail(f"这是 Ask-first 裁决：必须同时落决策记录。先写好 "
                 f"{docs_paths(cfg)['decisions']}/<日期>-<主题>.md，再 reply --decision-doc <路径>（AGENTS.md §5）")
        missing = [p for p in dec_docs if not (repo.root / p).exists()]
        if missing:
            fail("决策记录不存在：" + ", ".join(missing) + f"（落点目录 {docs_paths(cfg)['decisions']}/）")

    body = f"[FOREMAN:ANSWER] task={task['id']} dispatch={target['id']}\n{args.body}"
    transport = make_transport(repo, cfg)
    receipt = transport.send(target["worker_handle"], body)
    if was_human_gate:
        for g in task.get("gates", []):
            if not g.get("resolved"):
                g.update(resolved=True, decision=args.body, resolved_at=now_iso())
                if cfg.get("transport") == "orca" and g.get("native_id"):
                    transport.gate_resolve(g["native_id"], args.body)
        task.setdefault("decisions", []).append({"level": "T3", "at": now_iso(), "note": args.body})
        task.setdefault("docs", {}).setdefault("decisions", [])
        for rel in dec_docs:
            if rel not in task["docs"]["decisions"]:
                task["docs"]["decisions"].append(rel)
    else:
        task.setdefault("decisions", []).append({"level": "T2", "at": now_iso(), "note": args.body})
    repo.set_status(task, "running")
    repo.log_task(task, "running", f"ANSWER → {target['id']}（{'T3 人类裁决' if was_human_gate else 'T2 主管裁决'}）：{args.body[:80]}")
    repo.save_task(task)
    write_text(repo.find_task_dir(task["id"]) / "brief.md", brief_text(cfg, task))
    emit(args, {"id": task["id"], "receipt_id": receipt.get("id") or receipt.get("messageId")
                or (receipt.get("result") or {}).get("messageId"), "status": task["status"],
                "level": "T3" if was_human_gate else "T2"},
         f"已答复 {target['worker_handle']}（{'Ask-first 人类裁决，决策记录已登记' if was_human_gate else '主管拍板'}），任务回到 running")
    return EXIT_OK


def cmd_ask(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    cfg = effective_config(repo)
    assert_coordinator(repo, cfg)
    task = repo.load_task(args.task_id)
    if task["status"] not in ("running", "waiting_answer"):
        fail(f"任务 {task['id']} 状态为 {task['status']}，只在 running/waiting_answer 时可设 T3 闸门")
    options = [o.strip() for o in args.options.split(",") if o.strip()] if args.options else []
    repo.set_status(task, "waiting_human")
    gate = {
        "question": args.question, "options": options, "at": now_iso(), "resolved": False,
        "dispatch": task["dispatches"][-1]["id"] if task["dispatches"] else None,
    }
    if cfg.get("transport") == "orca":
        orch_task = (task["dispatches"][-1].get("orchestration_task") if task["dispatches"] else None)
        if not orch_task:
            fail(f"任务 {task['id']} 没有 orchestration task id（需先在 Orca 模式下 dispatch）")
        transport = make_transport(repo, cfg)
        gate["native_id"] = transport.gate_create(orch_task, args.question, options)
    task.setdefault("gates", []).append(gate)
    repo.log_task(task, "waiting_human", f"T3 决策门：{args.question}" + (f"（原生 gate {gate.get('native_id')}）" if gate.get("native_id") else ""))
    repo.save_task(task)
    opts = " / ".join(options) if options else "（自由回答）"
    native = "\n原生决策门已创建，可在 Orca UI 中裁决。" if gate.get("native_id") else ""
    emit(args, {"id": task["id"], "status": "waiting_human", "question": args.question, "options": options},
         f"T3 决策门已立起，{task['id']} 挂起，等待人类裁决前不放行任何分支操作。{native}\n问人类：{args.question}\n选项：{opts}\n人类答复后：reply {task['id']} --body \"<裁决与理由>\"")
    return EXIT_OK


# ---------------------------------------------------------------- 命令：review


def in_scope(rel: str, scope: list[str]) -> bool:
    if not scope:
        return True
    return any(rel == s or rel.startswith(s.rstrip("/") + "/") for s in scope)


def scan_added_lines(diff_text: str) -> tuple[list[str], list[str]]:
    """扫描 diff 新增行：返回 (secret_hits, skip_hits)。"""
    secrets: list[str] = []
    skips: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        for label, pat in SECRET_PATTERNS:
            if pat.search(body):
                secrets.append(f"{label}: {body.strip()[:80]}")
        for marker in SKIP_MARKERS:
            if marker in body:
                skips.append(body.strip()[:80])
    return secrets, skips


def diff_vs_base(repo: Repo, base: str, cwd: Path) -> tuple[str, str, str]:
    """在 worktree 内取 HEAD 对 base 的 (name-only, full, shortstat) diff。"""
    mb = repo.git("merge-base", f"refs/heads/{base}", "HEAD", cwd=cwd).strip()
    names = repo.git("diff", "--name-only", mb, "HEAD", cwd=cwd).strip()
    full = repo.git("diff", mb, "HEAD", cwd=cwd)
    stat = repo.git("diff", "--shortstat", mb, "HEAD", cwd=cwd).strip()
    return names, full, stat


def run_acceptance(repo: Repo, cmd: str, cwd: Path, task_id: str, label: str) -> dict:
    argv = shlex.split(cmd)
    proc = run_cmd(argv, cwd=cwd, timeout=1800)
    log = f"$ {cmd}\nexit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
    save_evidence(repo, task_id, f"acceptance-{label}.log", log)
    return {"cmd": cmd, "exit": proc.returncode,
            "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-2000:]}


def cmd_review(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    cfg = effective_config(repo)
    assert_coordinator(repo, cfg)
    task = repo.load_task(args.task_id)
    if task["status"] != "review":
        fail(f"任务 {task['id']} 状态为 {task['status']}，只有收到 DONE（review）后可审查")
    dispatch = task["dispatches"][-1]
    wt = Path(dispatch["worktree"])
    if not wt.exists():
        fail(f"worktree 不存在：{wt}；先查 dispatch 记录")

    names, full_diff, shortstat = diff_vs_base(repo, cfg["base"], wt)
    files = [n for n in names.splitlines() if n.strip()]
    out_scope = [f for f in files if not in_scope(f, task.get("scope", []))]
    secrets, skips = scan_added_lines(full_diff)

    checks = [
        {"name": "scope", "ok": not out_scope,
         "detail": "越界文件：" + ", ".join(out_scope) if out_scope else f"改动 {len(files)} 个文件全部在范围内"},
        {"name": "secrets", "ok": not secrets, "detail": "; ".join(secrets[:5]) if secrets else "未发现密钥痕迹"},
        {"name": "no-skip", "ok": not skips, "detail": "; ".join(skips[:5]) if skips else "未发现跳测试/--no-verify 痕迹"},
        {"name": "non-empty", "ok": bool(files), "detail": f"{shortstat or '无改动'}"},
    ]
    acceptance = None
    if task.get("acceptance"):
        acceptance = run_acceptance(repo, task["acceptance"], wt, task["id"], safe_name(dispatch["id"]))
        checks.append({"name": "acceptance", "ok": acceptance["exit"] == 0,
                       "detail": f"{task['acceptance']} exit={acceptance['exit']}"})
    else:
        checks.append({"name": "acceptance", "ok": False, "detail": "任务未定义验收命令"})

    # Ask-first（原 T3）裁决必须落决策库：过程态会消失，决策库不会（AGENTS.md §5）
    t3 = [x for x in task.get("decisions", []) if x.get("level") == "T3"]
    dec_docs = list((task.get("docs") or {}).get("decisions") or [])
    missing = [p for p in dec_docs if not (repo.root / p).exists()]
    if t3 and not dec_docs:
        dec_ok = False
        dec_detail = (f"本任务有 {len(t3)} 条 Ask-first 裁决但未登记决策记录："
                      f"写好 {docs_paths(cfg)['decisions']}/ 下的文件后用 reply --decision-doc 登记，再重跑 review")
    elif missing:
        dec_ok, dec_detail = False, "决策记录文件不存在：" + ", ".join(missing)
    elif t3:
        dec_ok, dec_detail = True, f"Ask-first 裁决已落决策库：{', '.join(dec_docs)}"
    else:
        dec_ok, dec_detail = True, "无 Ask-first 裁决，无需决策记录"
    checks.append({"name": "docs-decisions", "ok": dec_ok, "detail": dec_detail})

    progress_rel = progress_rel_for(cfg, task)

    review = {
        "at": now_iso(), "dispatch": dispatch["id"], "files": files, "shortstat": shortstat,
        "checks": checks, "acceptance": acceptance,
        "manual_items": [
            "逐个提交核对：每个提交对应一件事，提交信息说明为什么",
            f"merge 后 report 会把竣工单写到 {progress_rel}（progress 是归档后的唯一事实源）",
            "全量映射/枚举/单位类改动核对覆盖数 n/n 或点名未覆盖项",
        ],
    }
    task["review"] = review
    ok = all(c["ok"] for c in checks)
    if ok:
        repo.set_status(task, "merge_ready")
        repo.log_task(task, "merge_ready", f"审查通过：{shortstat}；验收 exit=0")
    else:
        if task["attempts"] >= cfg.get("max_attempts", 3):
            repo.set_status(task, "blocked")
            repo.log_task(task, "blocked", f"审查不通过且已达 {task['attempts']} 次尝试")
        else:
            repo.set_status(task, "failed")
            failed = [c["name"] for c in checks if not c["ok"]]
            repo.log_task(task, "failed", f"审查不通过：{', '.join(failed)}；修正后重新 dispatch")
    repo.save_task(task)

    summary = ("审查通过，可 merge" if ok else "审查不通过："
               + ", ".join(c["name"] for c in checks if not c["ok"]))
    emit(args, {"id": task["id"], "status": task["status"], "ok": ok, "checks": checks,
                "manual_items": review["manual_items"], "shortstat": shortstat},
         f"{task['id']} 审查：{summary}（{shortstat}）\n"
         + "\n".join(f"  [{'OK ' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}" for c in checks)
         + "\n人工复核项（主管自行判断）：\n" + "\n".join(f"  - {m}" for m in review["manual_items"]))
    return EXIT_OK if ok else EXIT_ERROR


# ---------------------------------------------------------------- 命令：merge


def cmd_merge(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    cfg = effective_config(repo)
    assert_coordinator(repo, cfg)
    task = repo.load_task(args.task_id)
    if task["status"] != "merge_ready":
        fail(f"任务 {task['id']} 状态为 {task['status']}，只有 merge_ready 可合并")
    if cfg.get("require_human_review"):
        fail("config.require_human_review=true：目标仓库要求人类评审，主管只做到 merge_ready，合并需人类执行")
    if cfg["base"] != repo.current_branch():
        fail(f"当前分支 {repo.current_branch()} 不是 base（{cfg['base']}），先切回 base")
    if not repo.is_clean():
        fail("工作区不干净，先提交或 stash")
    dispatch = task["dispatches"][-1]
    branch = dispatch.get("branch") or ""
    wt = Path(dispatch["worktree"])
    if not branch:
        if not wt.exists():
            fail(f"派工记录缺分支名且 worktree 不存在：{wt}")
        branch = repo.git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt).strip()
        dispatch["branch"] = branch
        repo.save_task(task)
    if not wt.exists():
        fail(f"worktree 不存在：{wt}；先查 dispatch 记录")

    merge_msg = f"Merge {branch}: {task['title']} (foreman {task['id']}/{dispatch['id']})"
    repo.git("merge", "--no-ff", branch, "-m", merge_msg)
    acceptance = None
    if task.get("acceptance"):
        acceptance = run_acceptance(repo, task["acceptance"], repo.root, task["id"], f"post-merge-{safe_name(dispatch['id'])}")
        if acceptance["exit"] != 0:
            repo.log_task(task, "merge-verify-failed",
                          f"合并后复跑验收失败 exit={acceptance['exit']}；保持 merge_ready，人工决定回退或修复")
            repo.save_task(task)
            fail("合并后复跑验收未通过：合并提交已存在，任务保持 merge_ready；"
                 "请人工决定 revert 或在 base 上修复（不要自动重跑）", EXIT_ERROR)
    merge_commit = repo.git("rev-parse", "--short", "HEAD").strip()
    task["merge"] = {
        "at": now_iso(), "branch": branch, "commit": merge_commit,
        "acceptance": acceptance, "dispatch": dispatch["id"],
    }
    repo.set_status(task, "completed")
    repo.log_task(task, "completed", f"合并 {merge_commit}，复跑验收通过，开始清理")

    # 清理：删 worktree 与分支（completed 定义的一部分；Orca 模式走 transport）
    if cfg.get("transport") == "orca":
        done = make_transport(repo, cfg).cleanup(repo, dispatch)
        repo.log_task(task, "cleaned", "; ".join(done) or "无清理动作")
    else:
        if wt.exists():
            repo.git("worktree", "remove", "--force", str(wt))
        repo.git("branch", "-D", branch)
        repo.log_task(task, "cleaned", f"已删除 worktree 与分支 {branch}")
    repo.save_task(task)

    # 归档：把任务目录移到 archive/，后续 report/show 自动从 archive 读
    src = repo.task_dir(task["id"])
    dst = repo.archive_dir / task["id"]
    if src.exists() and src != dst:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(src), str(dst))
    emit(args, {"id": task["id"], "status": "completed", "commit": merge_commit, "branch": branch},
         f"{task['id']} 完成：合并 {merge_commit}（--no-ff），复跑验收通过，worktree/分支已删，任务归档到 {dst}\n收尾运行 report {task['id']} 生成竣工单。默认不 push。")
    return EXIT_OK


# ---------------------------------------------------------------- 命令：report


def cmd_report(args: argparse.Namespace) -> int:
    repo = Repo(Path(args.repo))
    task = repo.load_task(args.task_id)
    d = repo.find_task_dir(task["id"])
    merge = task.get("merge") or {}
    review = task.get("review") or {}
    acc_logs = sorted((d / "evidence").glob("acceptance-*.log")) if (d / "evidence").exists() else []

    def gates_line() -> str:
        rows = [f"- [{g['at']}] {g['question']} → {g.get('decision', '（未裁决）')}"
                for g in task.get("gates", [])]
        return "\n".join(rows) or "- 无（全程 T2 内解决）"

    def decisions_line() -> str:
        rows = [f"- [{x['at']}] {x['level']}：{x['note']}" for x in task.get("decisions", [])]
        return "\n".join(rows) or "- 无记录"

    def docs_line() -> str:
        rows = [f"- {p}" for p in (task.get("docs") or {}).get("decisions") or []]
        return "\n".join(rows) or "- 无（无 Ask-first 裁决）"

    def evidence_line() -> str:
        files = sorted(p.name for p in (d / "evidence").glob("*")) if (d / "evidence").exists() else []
        return "\n".join(f"- evidence/{f}" for f in files) or "- 无"

    acc_summary = []
    for lg in acc_logs:
        first = lg.read_text(encoding="utf-8").splitlines()
        cmd_line = next((x for x in first if x.startswith("$ ")), "?")
        exit_line = next((x for x in first if x.startswith("exit=")), "exit=?")
        acc_summary.append(f"- `{cmd_line[2:]}` {exit_line}（evidence/{lg.name}）")

    branch = merge.get('branch') or (task['dispatches'][-1]['branch'] if task['dispatches'] else '（无）')
    status_cn = STATUS_CN.get(task['status'], DEFAULT_STATUS_CN)
    report = f"""# {task['id']}：{task['title']}

- 任务描述：{task.get('goal', task['title'])}
- 完成状态：{status_cn}（尝试 {task['attempts']} 次，派工 {len(task['dispatches'])} 次）
- 生成时间：{now_iso()}
- 分支：{branch}｜合并提交：{merge.get('commit', '（未合并）')}｜改动面：{review.get('shortstat', '（未审查）')}

## 验证方式（可复现）

{chr(10).join(acc_summary) or '- 无验收记录'}

合并后复跑验收的日志同样在 evidence/ 下（`post-merge-*.log`）；归档前完整清单：

{evidence_line()}

## 裁决与决策

### 主管拍板（T2）

{decisions_line()}

### 人类闸（Ask-first）

{gates_line()}

### 决策记录（docs/decisions/）

{docs_line()}

## 遗留问题

- 无
"""
    rel = progress_rel_for(repo.load_or_default_config(), task)
    path = repo.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, report)
    # 过程态会消失，留一个指针，让清理后仍能从 archive 找到落点
    write_text(d / "report.path", rel + "\n")
    emit(args, {"id": task["id"], "status": task["status"], "report": str(path), "rel": rel},
         report + f"\n竣工单已写入：{rel}（入库；默认不 push。过程态副本：archive/{task['id']}/）")
    return EXIT_OK


# ---------------------------------------------------------------- CLI 装配


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="foreman", description="vibe-base 执行编排控制面 CLI")
    p.add_argument("--version", action="version", version=f"foreman {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_json(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--json", action="store_true", help="输出机器可读 JSON")

    sp = sub.add_parser("doctor", help="环境与仓库体检")
    sp.add_argument("--repo", default=".")
    add_json(sp)
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("init", help="初始化 .vibe/ 与忽略规则（幂等）")
    sp.add_argument("--repo", default=".")
    sp.add_argument("--base", help="base 分支，默认当前分支")
    sp.add_argument("--agent", help="兜底 worker agent（默认按主管会话自动选择）")
    sp.add_argument("--transport", choices=["hcom", "orca", "auto"],
                    help="编排通道：auto（默认，Orca 终端→orca 否则 hcom）/ hcom / orca")
    sp.add_argument("--foreman-name", help="主管在编排通道里的身份（hcom 模式=会话名；worker 上报目标）")
    add_json(sp)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("task", help="任务管理")
    tsub = sp.add_subparsers(dest="task_command", required=True)

    sp2 = tsub.add_parser("new", help="新建任务")
    sp2.add_argument("--repo", default=".")
    sp2.add_argument("--title", required=True)
    sp2.add_argument("--goal", help="目标与背景描述，默认用 title")
    sp2.add_argument("--spec", help="规格文档路径")
    sp2.add_argument("--scope", action="append",
                     help="允许改动的路径，可多次传或逗号分隔；缺省=全库")
    sp2.add_argument("--accept", help="验收命令（在 worktree 内运行）")
    sp2.add_argument("--agent", help="worker agent，默认取 config")
    sp2.add_argument("--notes", help="补充说明")
    add_json(sp2)
    sp2.set_defaults(func=cmd_task_new)

    sp2 = tsub.add_parser("set-acceptance", help="定义/修改验收命令")
    sp2.add_argument("--repo", default=".")
    sp2.add_argument("task_id")
    sp2.add_argument("--accept", required=True)
    add_json(sp2)
    sp2.set_defaults(func=cmd_task_set_acceptance)

    sp2 = tsub.add_parser("abandon", help="放弃当前派工（worker 死亡/不可恢复）")
    sp2.add_argument("--repo", default=".")
    sp2.add_argument("task_id")
    sp2.add_argument("--reason", required=True, help="放弃原因（写入 task.log）")
    add_json(sp2)
    sp2.set_defaults(func=cmd_task_abandon)

    sp2 = tsub.add_parser("set-scope", help="调整允许改动的范围（T2 裁决落地）")
    sp2.add_argument("--repo", default=".")
    sp2.add_argument("task_id")
    sp2.add_argument("--scope", action="append", required=True,
                     help="允许改动的路径，可多次传或逗号分隔")
    add_json(sp2)
    sp2.set_defaults(func=cmd_task_set_scope)

    sp2 = tsub.add_parser("list", help="任务列表")
    sp2.add_argument("--repo", default=".")
    add_json(sp2)
    sp2.set_defaults(func=cmd_task_list)

    sp2 = tsub.add_parser("show", help="任务详情")
    sp2.add_argument("--repo", default=".")
    sp2.add_argument("task_id")
    add_json(sp2)
    sp2.set_defaults(func=cmd_task_show)

    sp = sub.add_parser("dispatch", help="建 worktree/branch 并拉起 worker")
    sp.add_argument("task_id")
    sp.add_argument("--repo", default=".")
    sp.add_argument("--agent", help="worker agent，默认取任务/config")
    sp.add_argument("--headless", action="store_true", help="无终端环境必须显式开启")
    add_json(sp)
    sp.set_defaults(func=cmd_dispatch)

    sp = sub.add_parser("events", help="阻塞等待 worker 的协议消息并落状态机")
    sp.add_argument("--repo", default=".")
    sp.add_argument("--timeout", type=int, default=300,
                    help="单轮等待上限秒数（同一 dispatch 最多两轮，之后结束回合留状态行）")
    add_json(sp)
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("reply", help="把裁决（T2/T3）作为 ANSWER 发回 worker")
    sp.add_argument("task_id")
    sp.add_argument("--repo", default=".")
    sp.add_argument("--dispatch", help="目标派工记录，默认最近一次")
    sp.add_argument("--body", required=True, help="裁决与理由")
    sp.add_argument("--decision-doc", action="append",
                    help="Ask-first 裁决对应的 docs/decisions/ 文件（相对仓库根，可多次；缺登记 review 会拦 merge）")
    add_json(sp)
    sp.set_defaults(func=cmd_reply)

    sp = sub.add_parser("ask", help="立起 T3 决策门（任务挂起等人类）")
    sp.add_argument("task_id")
    sp.add_argument("--repo", default=".")
    sp.add_argument("--question", required=True)
    sp.add_argument("--options", help="逗号分隔的选项")
    add_json(sp)
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("review", help="审 diff + 跑验收 + 存证据")
    sp.add_argument("task_id")
    sp.add_argument("--repo", default=".")
    add_json(sp)
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("merge", help="--no-ff 合并、复跑验收、删树、归档")
    sp.add_argument("task_id")
    sp.add_argument("--repo", default=".")
    add_json(sp)
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("report", help="生成竣工单")
    sp.add_argument("task_id")
    sp.add_argument("--repo", default=".")
    add_json(sp)
    sp.set_defaults(func=cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        eprint("中断")
        sys.exit(130)
