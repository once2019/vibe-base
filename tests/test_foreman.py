"""foreman.py 单元 + 集成测试。

- 单元：协议头解析、原子写、slug、scope 判定、密钥/跳测扫描、hcom 回执解析。
- 集成：临时 git 仓库 + fixtures/hcom 假命令，验证 init/task/dispatch/events/review/merge 全链路。
不调用真实 hcom 与网络。
"""

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "vibe-base" / "assets" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import foreman  # noqa: E402

FIXTURE_HCOM = Path(__file__).resolve().parent / "fixtures" / "hcom"
FIXTURE_ORCA = Path(__file__).resolve().parent / "fixtures" / "orca"


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class TempGitRepoMixin(unittest.TestCase):
    """每个测试一个临时 git 仓库；fake hcom 通过 config.hcom_bin + env 注入。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="foreman-test-"))
        logs = self.root / ".vibe" / "logs"
        logs.mkdir(parents=True)
        os.environ["FAKE_HCOM_LOG"] = str(logs / "hcom-log.jsonl")
        os.environ.pop("FAKE_HCOM_EVENT", None)
        # 隔离宿主会话标记：否则跑测试的 agent（pi/codex）会污染 worker agent 探测
        foreman._CONTEXT_CACHE.clear()
        foreman._TRANSPORT_CACHE.clear()
        self.addCleanup(foreman._CONTEXT_CACHE.clear)
        self.addCleanup(foreman._TRANSPORT_CACHE.clear)
        # 也不让宿主进程树参与探测（否则跑在 pi/codex 里的测试会得到宿主身份）
        self._saved_ancestor = foreman.ancestor_commands
        foreman.ancestor_commands = lambda limit=16: []
        self._saved_agent_env = {k: os.environ.pop(k, None)
                                 for k in ("PI_CODING_AGENT", "PI_SESSION_ID", "CODEX_THREAD_ID",
                                           "CLAUDECODE", "ORCA_TERMINAL_HANDLE",
                                           "FAKE_ORCA_WORKER_TERMINAL")}
        os.environ.pop("FAKE_HCOM_LAUNCH", None)
        self.orca_log = logs / "orca-log.jsonl"
        os.environ["FAKE_ORCA_LOG"] = str(self.orca_log)
        os.environ["ORCA_CLI_COMMAND"] = str(FIXTURE_ORCA)
        os.environ["FAKE_ORCA_REPO_ROOT"] = str(self.root)
        os.environ.pop("FAKE_ORCA_EVENT", None)
        make_executable(FIXTURE_ORCA)
        run_git(self.root, "init", "-q")
        run_git(self.root, "config", "user.email", "t@t")
        run_git(self.root, "config", "user.name", "t")
        foreman.write_text(self.root / ".git" / "info" / "exclude", ".vibe\n")
        (self.root / "README.md").write_text("demo\n", encoding="utf-8")
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-q", "-m", "init")
        self.repo = foreman.Repo(self.root)
        foreman.write_json(self.repo.config_path, {
            "foreman_name": "foreman", "tag": "foreman", "default_agent": "codex",
            "base": "master", "max_attempts": 3, "require_human_review": False,
            "hcom_bin": str(FIXTURE_HCOM),
        })
        make_executable(FIXTURE_HCOM)

    def tearDown(self) -> None:
        if hasattr(self, "wt") and self.wt.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(self.wt)],
                           cwd=self.root, check=False, capture_output=True)
        subprocess.run(["chmod", "-R", "u+w", str(self.root)], check=False)
        subprocess.run(["rm", "-rf", str(self.root)], check=False)
        if hasattr(self, "wt"):
            subprocess.run(["rm", "-rf", str(self.wt.parent)], check=False)
        os.environ.pop("FAKE_HCOM_EVENT", None)
        # 隔离宿主会话标记：否则跑测试的 agent（pi/codex）会污染 worker agent 探测
        foreman._CONTEXT_CACHE.clear()
        foreman._TRANSPORT_CACHE.clear()
        self.addCleanup(foreman._CONTEXT_CACHE.clear)
        self.addCleanup(foreman._TRANSPORT_CACHE.clear)
        # 也不让宿主进程树参与探测（否则跑在 pi/codex 里的测试会得到宿主身份）
        self._saved_ancestor = foreman.ancestor_commands
        foreman.ancestor_commands = lambda limit=16: []
        self._saved_agent_env = {k: os.environ.pop(k, None)
                                 for k in ("PI_CODING_AGENT", "PI_SESSION_ID", "CODEX_THREAD_ID",
                                           "CLAUDECODE", "ORCA_TERMINAL_HANDLE",
                                           "FAKE_ORCA_WORKER_TERMINAL")}
        os.environ.pop("ORCA_CLI_COMMAND", None)
        os.environ.pop("FAKE_ORCA_EVENT", None)

    def set_event(self, text: str) -> None:
        subject, _, body = text.partition("\n")
        ev_file = self.root / ".vibe" / "logs" / "fake-event.json"
        ev_file.write_text(json.dumps(
            {"id": 42, "ts": "2026-11-12T00:00:00Z", "type": "message",
             "instance": "w-T-001", "data": {"from": "w-T-001", "text": text, "intent": "request"}},
            ensure_ascii=False), encoding="utf-8")
        os.environ["FAKE_HCOM_EVENT"] = str(ev_file)
        orca_ev = self.root / ".vibe" / "logs" / "fake-orca-event.json"
        orca_ev.write_text(json.dumps(
            {"id": "m-42", "from": "dispatch:d-77", "subject": subject, "body": body},
            ensure_ascii=False), encoding="utf-8")
        os.environ["FAKE_ORCA_EVENT"] = str(orca_ev)

    def full_task(self, accept: str | None = "true", scope: str = "src/") -> dict:
        (self.root / "src").mkdir()
        (self.root / "src" / "keep.txt").write_text("x\n", encoding="utf-8")
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-q", "-m", "add src")
        args = ["task", "new", "--repo", str(self.root), "--title", "实现 X", "--scope", scope]
        if accept:
            args += ["--accept", accept]
        rc = foreman.main(args)
        assert rc == 0
        return self.repo.load_task("T-001")

    def dispatch(self, extra: list[str] | None = None) -> None:
        rc = foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--headless",
                           *(extra or [])])
        assert rc == 0, "dispatch 应成功"

    def hcom_calls(self) -> list[list[str]]:
        log = self.root / ".vibe" / "logs" / "hcom-log.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()]

    def orca_calls(self) -> list[list[str]]:
        return [json.loads(x) for x in self.orca_log.read_text().splitlines()]

    def run_events(self) -> int:
        return foreman.main(["events", "--repo", str(self.root), "--timeout", "5", "--json"])

    def commit_in(self, wt: Path, msg: str) -> None:
        run_git(wt, "add", ".")
        run_git(wt, "commit", "-q", "-m", msg)

    def write_decision_doc(self, name: str = "2026-11-13-test-decision.md") -> str:
        """Ask-first 裁决必须落决策库：测试里给一份可登记的记录，返回相对路径。"""
        rel = f"docs/decisions/{name}"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# 测试决策 {name}\n\n- 结论：允许\n", encoding="utf-8")
        return rel


class TestUnits(unittest.TestCase):
    def test_header_candidates(self) -> None:
        self.assertEqual(foreman.header_candidates("[FOREMAN:ASK] task=T-001 dispatch=D-001\nq"),
                         [("ASK", "T-001", "D-001")])
        self.assertEqual(foreman.header_candidates("no header"), [])
        self.assertEqual(foreman.header_candidates(""), [])
        # 同一封里两个不同协议头：调用方必须按歧义失败，这里只保证去重保序
        two = ("[FOREMAN:DONE] task=T-001 dispatch=D-001\n"
               "[FOREMAN:DONE] task=T-001 dispatch=D-001\n"
               "[FOREMAN:ASK] task=T-002 dispatch=D-002\n")
        self.assertEqual(foreman.header_candidates(two),
                         [("DONE", "T-001", "D-001"), ("ASK", "T-002", "D-002")])

    def test_slugify(self) -> None:
        self.assertEqual(foreman.slugify("实现 X!"), "实现-x")
        self.assertEqual(foreman.slugify("///"), "task")
        self.assertTrue(len(foreman.slugify("x" * 100)) <= 40)

    def test_write_json_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "a.json"
            foreman.write_json(p, {"a": 1})
            self.assertEqual(json.loads(p.read_text()), {"a": 1})
            self.assertEqual(list(p.parent.glob("*.tmp")), [])
            foreman.write_json(p, {"b": 2})
            self.assertEqual(json.loads(p.read_text()), {"b": 2})

    def test_transitions_gate_fail_visible(self) -> None:
        repo = foreman.Repo(Path("."))
        with self.assertRaises(SystemExit):
            repo.set_status({"id": "T-x", "status": "completed"}, "running")

    def test_in_scope(self) -> None:
        self.assertTrue(foreman.in_scope("src/a.py", []))
        self.assertTrue(foreman.in_scope("src/a.py", ["src/"]))
        self.assertTrue(foreman.in_scope("src/a.py", ["src/a.py"]))
        self.assertFalse(foreman.in_scope("srcx/a.py", ["src/"]))
        self.assertFalse(foreman.in_scope("docs/b.md", ["src/"]))

    def test_scan_added_lines(self) -> None:
        diff = "+token = 'supersecretvalue123'\n-ok\n+git commit --no-verify\n+++ b/f\n"
        secrets, skips = foreman.scan_added_lines(diff)
        self.assertEqual(len(secrets), 1)
        self.assertEqual(len(skips), 1)

    def test_hcom_wait_message_takes_last_json_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = foreman.Repo(Path(td))
            h = foreman.Hcom(repo, {"hcom_bin": "x", "foreman_name": "foreman"})
            payload = json.dumps({"id": 7, "type": "message", "instance": "w", "data": {}})
            fake = Path(td) / "fake-hcom"
            fake.write_text(f"#!/bin/sh\necho '<hcom>luna → foreman</hcom>'\necho '{payload}'\n")
            make_executable(fake)
            h.bin = str(fake)
            self.assertEqual(h.wait_message(5)["id"], 7)

    def test_orca_skipped_messages_are_logged(self) -> None:
        """非协议消息（heartbeat/escalation）被 check 消费时必须留痕，不静默丢弃。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = foreman.Repo(root)
            msgs = [{"id": "m-1", "type": "heartbeat", "from": "dispatch:d-1", "subject": "alive"},
                    {"id": "m-2", "type": "escalation", "from": "dispatch:d-1",
                     "subject": "Worker stopped without reporting"}]
            fake = root / "fake-orca-skip"
            fake.write_text("#!/bin/sh\necho '" + json.dumps({"ok": True, "result": {"messages": msgs}}) + "'\n")
            make_executable(fake)
            o = foreman.Orca(repo, {"foreman_name": "foreman"})
            o.bin = str(fake)
            self.assertTrue(o.wait_message(5).get("timed_out"))
            log = (root / ".vibe" / "logs" / "transport-skipped.log").read_text(encoding="utf-8")
            self.assertIn("heartbeat", log)
            self.assertIn("Worker stopped without reporting", log)

    def test_hcom_wait_message_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = foreman.Repo(Path(td))
            h = foreman.Hcom(repo, {"hcom_bin": "x", "foreman_name": "foreman"})
            fake = Path(td) / "fake-hcom-timeout"
            fake.write_text("#!/bin/sh\necho '{\"timed_out\": true}'\nexit 1\n")
            make_executable(fake)
            h.bin = str(fake)
            self.assertTrue(h.wait_message(5).get("timed_out"))


class TestInitAndTask(TempGitRepoMixin):
    def test_two_same_title_tasks_get_distinct_progress_paths(self) -> None:
        """同一天同名任务不得写同一个竣工单（否则后一个静默覆盖前一个）。"""
        for _ in range(2):
            self.assertEqual(foreman.main(["task", "new", "--repo", str(self.root),
                                           "--title", "实现 X", "--accept", "true"]), 0)
        a = self.repo.load_task("T-001")["docs"]["progress"]
        b = self.repo.load_task("T-002")["docs"]["progress"]
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("docs/progress/"))

    def test_uninitialized_repo_gives_readable_error(self) -> None:
        """未 init 时不能抛 traceback：控制面命令必须给可执行提示（新项目首触点）。"""
        self.repo.config_path.unlink()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                foreman.main(["task", "new", "--repo", str(self.root),
                              "--title", "X", "--accept", "true"])
        msg = err.getvalue()
        self.assertIn(".vibe 未初始化", msg)
        self.assertIn("init --repo .", msg)
        self.assertNotIn("Traceback", msg)

    def test_init_writes_exclude_idempotent(self) -> None:
        self.assertEqual(foreman.main(["init", "--repo", str(self.root)]), 0)
        exclude = (self.root / ".git" / "info" / "exclude").read_text()
        self.assertIn(".vibe", exclude)
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=self.root,
                                        capture_output=True, text=True).stdout.strip(), "")
        foreman.main(["init", "--repo", str(self.root), "--base", "other"])  # 显式覆盖生效，其余字段保留
        self.assertEqual(self.repo.config()["base"], "other")
        self.assertEqual(self.repo.config()["foreman_name"], "foreman")
        foreman.main(["init", "--repo", str(self.root), "--foreman-name", "sumo"])
        self.assertEqual(self.repo.config()["foreman_name"], "sumo")

    def test_task_new_ready_with_acceptance_pending_without(self) -> None:
        task = self.full_task()
        self.assertEqual(task["status"], "ready")
        self.assertEqual(task["id"], "T-001")
        brief = (self.repo.task_dir("T-001") / "brief.md").read_text(encoding="utf-8")
        self.assertIn("[FOREMAN:<TYPE>] task=T-001", brief)
        self.assertIn("ASK", brief)
        self.assertIn("```bash\ntrue\n```", brief)
        # worker 必须自审后才上报：派工单里要有可执行的四查，不是一句"自测通过"
        self.assertIn("## 自审（发 DONE 之前必须做", brief)
        self.assertIn("git diff master...HEAD", brief)
        for item in ("范围：", "验收：", "无跳过：", "无密钥："):
            self.assertIn(item, brief)
        self.assertIn("不采信自报", brief)
        self.assertEqual(foreman.main(["task", "new", "--repo", str(self.root), "--title", "第二个"]), 0)
        self.assertEqual(self.repo.load_task("T-002")["status"], "pending")
        with self.assertRaises(SystemExit):  # 无验收命令不允许派工
            foreman.main(["dispatch", "T-002", "--repo", str(self.root)])
        self.assertEqual(foreman.main(["task", "set-acceptance", "T-002", "--repo",
                                       str(self.root), "--accept", "true"]), 0)
        self.assertEqual(self.repo.load_task("T-002")["status"], "ready")

    def test_task_ids_increment_across_archive(self) -> None:
        self.full_task()
        self.repo.archive_dir.mkdir()
        self.repo.task_dir("T-001").rename(self.repo.archive_dir / "T-001")
        self.assertEqual(self.repo.next_task_id(), "T-002")


class TestDispatch(TempGitRepoMixin):
    def test_dispatch_creates_tree_and_calls_hcom(self) -> None:
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["attempts"], 1)
        d = task["dispatches"][0]
        self.assertEqual(d["id"], "D-001")
        self.assertEqual(d["worker_handle"], "w-T-001")
        self.assertEqual(d["branch"], "foreman/T-001-实现-x")
        self.assertTrue(Path(d["worktree"]).exists())
        launch = next(c for c in self.hcom_calls() if c[0] == "codex")
        self.assertEqual(launch[launch.index("--name") + 1], "w-T-001")
        self.assertIn("--dir", launch)
        self.assertIn("--headless", launch)
        prompt = launch[launch.index("--hcom-prompt") + 1]
        self.assertIn("FOREMAN:ASK", prompt)
        self.assertIn("D-001", prompt)
        self.assertIn("brief.md", prompt)
        self.assertIn("发 DONE 之前先自审", prompt)
        self.assertIn("自审四项结论", prompt)

    def test_dispatch_refuses_duplicate_tree(self) -> None:
        self.full_task()
        self.dispatch()
        with self.assertRaises(SystemExit):
            foreman.main(["dispatch", "T-001", "--repo", str(self.root)])

    def test_launch_failure_cleans_new_tree_and_allows_retry(self) -> None:
        self.full_task()
        broken = self.root / ".vibe" / "logs" / "broken-hcom"
        broken.write_text("#!/bin/sh\necho 'no terminal emulator found' >&2\nexit 1\n")
        make_executable(broken)
        foreman.write_json(self.repo.config_path,
                           {**self.repo.config(), "hcom_bin": str(broken)})
        with self.assertRaises(SystemExit):
            foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--headless"])
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "ready")  # 回滚
        self.assertFalse((self.root / ".vibe" / "worktrees" / "T-001").exists())  # 空树已清理
        self.assertEqual(subprocess.run(["git", "branch", "--list", "foreman/T-001-实现-x"],
                                        cwd=self.root, capture_output=True, text=True).stdout.strip(), "")
        self.assertIn("dispatch-failed", json.dumps(task["log"]))
        # 恢复正常 hcom 后可直接重派，不受残留树阻塞
        foreman.write_json(self.repo.config_path,
                           {**self.repo.config(), "hcom_bin": str(FIXTURE_HCOM)})
        self.dispatch()
        self.assertEqual(self.repo.load_task("T-001")["status"], "running")


class TestEventFlow(TempGitRepoMixin):
    def setUp(self) -> None:
        super().setUp()
        self.full_task()
        self.dispatch()

    def test_ask_moves_to_waiting_answer_then_reply(self) -> None:
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n用哪个库？选项 A/B，推荐 A")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_answer")
        self.assertTrue((self.repo.task_dir("T-001") / "evidence" / "ask-D-001.md").exists())

        self.set_event("没有协议头的消息")
        with self.assertRaises(SystemExit):  # 缺协议头必须 fail-visible
            self.run_events()

        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root),
                                       "--dispatch", "D-001", "--body", "选 A，因为 X"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["decisions"][0]["level"], "T2")

    def test_done_review_merge_happy_path(self) -> None:
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(wt, "feat: x")

        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "review")

        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root), "--json"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "merge_ready")
        self.assertTrue((self.repo.task_dir("T-001") / "evidence" / "acceptance-D-001.log").exists())

        self.assertEqual(foreman.main(["merge", "T-001", "--repo", str(self.root)]), 0)
        task = self.repo.load_task("T-001")  # 归档后仍可读
        self.assertEqual(task["status"], "completed")
        log = subprocess.run(["git", "log", "--oneline", "-2"], cwd=self.root,
                             capture_output=True, text=True).stdout
        self.assertIn("Merge foreman/T-001-实现-x", log)
        self.assertFalse(Path(task["dispatches"][0]["worktree"]).exists())
        self.assertEqual(subprocess.run(["git", "branch", "--list", task["dispatches"][0]["branch"]],
                                        cwd=self.root, capture_output=True, text=True).stdout.strip(), "")
        self.assertEqual(foreman.main(["report", "T-001", "--repo", str(self.root)]), 0)
        task = self.repo.load_task("T-001")
        progress = self.root / task["docs"]["progress"]
        self.assertTrue(progress.exists(), f"竣工单应写到注册的 progress 落点：{progress}")
        body = progress.read_text(encoding="utf-8")
        self.assertIn("完成状态：已完成", body)
        self.assertIn("## 验证方式（可复现）", body)
        self.assertIn("## 遗留问题", body)
        self.assertTrue((self.repo.archive_dir / "T-001" / "report.path").exists())

    def test_merge_refuses_when_not_merge_ready(self) -> None:
        with self.assertRaises(SystemExit):
            foreman.main(["merge", "T-001", "--repo", str(self.root)])

    def test_out_of_scope_fails_review_then_retry_then_blocked(self) -> None:
        # 第 1 次失败：越界改动
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "README.md").write_text("越界改动\n", encoding="utf-8")
        self.commit_in(wt, "oos")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 1)
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed")

        # 第 2 次：重派复用既有树，再次失败
        self.dispatch()
        task = self.repo.load_task("T-001")
        self.assertEqual(task["attempts"], 2)
        wt = Path(task["dispatches"][-1]["worktree"])
        (wt / "README.md").write_text("又越界\n", encoding="utf-8")
        self.commit_in(wt, "oos2")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-002\n完成")
        self.run_events()
        foreman.main(["review", "T-001", "--repo", str(self.root)])

        # 第 3 次失败 → blocked，且不得再派
        self.dispatch()
        wt = Path(self.repo.load_task("T-001")["dispatches"][-1]["worktree"])
        (wt / "README.md").write_text("还越界\n", encoding="utf-8")
        self.commit_in(wt, "oos3")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-003\n完成")
        self.run_events()
        foreman.main(["review", "T-001", "--repo", str(self.root)])
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "blocked")
        with self.assertRaises(SystemExit):
            foreman.main(["dispatch", "T-001", "--repo", str(self.root)])

    def test_t3_gate_suspends_until_reply(self) -> None:
        self.assertEqual(foreman.main(["ask", "T-001", "--repo", str(self.root),
                                       "--question", "允许改 schema？", "--options", "允许,不允许"]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_human")
        with self.assertRaises(SystemExit):  # 挂起期间 merge 不放行
            foreman.main(["merge", "T-001", "--repo", str(self.root)])
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root),
                                       "--body", "允许，理由 Z",
                                       "--decision-doc", self.write_decision_doc()]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["gates"][0]["resolved"], True)
        self.assertEqual(task["decisions"][-1]["level"], "T3")

    def test_events_timeout(self) -> None:
        os.environ.pop("FAKE_HCOM_EVENT", None)
        # 隔离宿主会话标记：否则跑测试的 agent（pi/codex）会污染 worker agent 探测
        foreman._CONTEXT_CACHE.clear()
        foreman._TRANSPORT_CACHE.clear()
        self.addCleanup(foreman._CONTEXT_CACHE.clear)
        self.addCleanup(foreman._TRANSPORT_CACHE.clear)
        # 也不让宿主进程树参与探测（否则跑在 pi/codex 里的测试会得到宿主身份）
        self._saved_ancestor = foreman.ancestor_commands
        foreman.ancestor_commands = lambda limit=16: []
        self._saved_agent_env = {k: os.environ.pop(k, None)
                                 for k in ("PI_CODING_AGENT", "PI_SESSION_ID", "CODEX_THREAD_ID",
                                           "CLAUDECODE", "ORCA_TERMINAL_HANDLE",
                                           "FAKE_ORCA_WORKER_TERMINAL")}
        self.assertEqual(self.run_events(), 1)

    def test_escalate_fails(self) -> None:
        self.set_event("[FOREMAN:ESCALATE] task=T-001 dispatch=D-001\n卡住了")
        self.run_events()
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed")
        evidence = self.repo.task_dir("T-001") / "evidence" / "escalate-D-001.md"
        self.assertIn("卡住了", evidence.read_text(encoding="utf-8"))

    def test_duplicate_done_is_idempotent(self) -> None:
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(self.run_events(), 0)  # 重发同一 DONE 不炸状态机
        self.assertEqual(self.repo.load_task("T-001")["status"], "review")


class TestOrcaTransport(TempGitRepoMixin):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        self.full_task()  # src/ 先提交到 master，再从 master 建假 worktree
        self.wt = Path(tempfile.mkdtemp(prefix="orca-wt-")) / "wt"
        run_git(self.root, "worktree", "add", str(self.wt), "-b", "foreman/T-001-impl", "master")
        os.environ["FAKE_ORCA_WT"] = str(self.wt)
        self.assertEqual(foreman.main(["dispatch", "T-001", "--repo", str(self.root)]), 0)

    def test_dispatch_uses_task_create_and_worker_start(self) -> None:
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "running")
        d = task["dispatches"][0]
        self.assertEqual(d["worker_handle"], "dispatch:d-77")
        self.assertEqual(d["orchestration_task"], "ot-1")
        self.assertEqual(d["worktree"], str(self.wt))
        self.assertEqual(d["branch"], "foreman/T-001-impl")
        calls = self.orca_calls()
        tc = next(c for c in calls if "task-create" in c)
        spec = tc[tc.index("--spec") + 1]
        self.assertIn("[FOREMAN:ASK]", spec)
        self.assertIn("run:run-1", spec)
        ws = next(c for c in calls if "worker-start" in c)
        self.assertEqual(ws[ws.index("--task") + 1], "ot-1")
        self.assertEqual(ws[ws.index("--repo") + 1], "id:repo-1")
        self.assertEqual(ws[ws.index("--base-branch") + 1], "master")
        self.assertEqual(ws[ws.index("--agent") + 1], "codex")
        self.assertNotIn("--dir", ws)  # 不建手工 worktree

    def test_ask_native_gate_and_reply_resolves(self) -> None:
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n用哪个库？")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_answer")
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root), "--body", "选 A"]), 0)
        sends = [c for c in self.orca_calls() if "send" in c]
        self.assertTrue(sends)
        self.assertEqual(sends[-1][sends[-1].index("--to") + 1], "dispatch:d-77")
        self.assertEqual(foreman.main(["ask", "T-001", "--repo", str(self.root),
                                       "--question", "允许改 schema？", "--options", "允许,不允许"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["gates"][-1]["native_id"], "gate-1")
        self.assertEqual(task["status"], "waiting_human")
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root), "--body", "允许",
                                       "--decision-doc", self.write_decision_doc()]), 0)
        gr = [c for c in self.orca_calls() if "gate-resolve" in c]
        self.assertTrue(gr)
        self.assertEqual(gr[-1][gr[-1].index("--id") + 1], "gate-1")
        self.assertEqual(self.repo.load_task("T-001")["status"], "running")

    def test_done_review_merge_cleanup_via_orca(self) -> None:
        (self.wt / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(self.wt, "feat: x")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "review")
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "merge_ready")
        self.assertEqual(foreman.main(["merge", "T-001", "--repo", str(self.root)]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "completed")
        self.assertIn("Merge foreman/T-001-impl", subprocess.run(
            ["git", "log", "--oneline", "-2"], cwd=self.root, capture_output=True, text=True).stdout)
        calls = self.orca_calls()
        tu = [c for c in calls if "task-update" in c]
        self.assertTrue(tu, "cleanup 必须结算 orchestration task")
        self.assertEqual(tu[-1][tu[-1].index("--status") + 1], "completed")
        self.assertIn("worker-release", [x for c in calls for x in c])
        rm = [c for c in calls if "rm" in c and "worktree" in c]
        self.assertTrue(rm)
        self.assertEqual(rm[-1][rm[-1].index("--worktree") + 1], f"repo-1::{self.wt}")
        self.assertIn("--delete-branch", rm[-1])

    def test_orca_events_timeout(self) -> None:
        os.environ.pop("FAKE_ORCA_EVENT", None)
        self.assertEqual(self.run_events(), 1)


class TestAskFirstDecisionRecord(TempGitRepoMixin):
    """Ask-first 裁决必须落决策库：reply 硬闸 + review 二道网。"""

    def ask_and_commit(self) -> Path:
        self.full_task()
        self.dispatch()
        self.assertEqual(foreman.main(["ask", "T-001", "--repo", str(self.root),
                                       "--question", "可以改 schema 吗",
                                       "--options", "允许,不允许"]), 0)
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "src" / "schema.txt").write_text("x\n", encoding="utf-8")
        self.commit_in(wt, "feat: schema")
        return wt

    def test_reply_refuses_ask_first_without_decision_doc(self) -> None:
        self.ask_and_commit()
        with self.assertRaises(SystemExit):
            foreman.main(["reply", "T-001", "--repo", str(self.root), "--body", "允许"])
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_human")

    def test_reply_refuses_missing_decision_doc_file(self) -> None:
        self.ask_and_commit()
        with self.assertRaises(SystemExit):
            foreman.main(["reply", "T-001", "--repo", str(self.root), "--body", "允许",
                          "--decision-doc", "docs/decisions/2026-11-14-schema.md"])
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_human")

    def test_registered_decision_doc_passes_review(self) -> None:
        self.ask_and_commit()
        doc = self.root / "docs" / "decisions" / "2026-11-14-schema.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("# 允许改 schema\n", encoding="utf-8")
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root), "--body", "允许",
                                       "--decision-doc", "docs/decisions/2026-11-14-schema.md"]), 0)
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        self.assertIn("docs-decisions", buf.getvalue())
        self.assertEqual(self.repo.load_task("T-001")["status"], "merge_ready")

    def test_decision_doc_must_be_relative_and_inside_decisions(self) -> None:
        self.ask_and_commit()
        (self.root / "outside.md").write_text("# 仓库根但不在决策目录\n", encoding="utf-8")
        (self.root / "docs" / "decisions").mkdir(parents=True)  # 让 .. 真能解析到 outside.md
        for bad in ("/etc/passwd", "../outside.md", "docs/design/2026-11-14-x.md",
                    "docs/decisions/../../outside.md"):  # 绝对 / 逃出 / 错目录 / 前缀合法但穿越（目标真实存在）
            with self.assertRaises(SystemExit):
                foreman.main(["reply", "T-001", "--repo", str(self.root), "--body", "允许",
                              "--decision-doc", bad])
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_human")

    def test_legacy_task_with_t3_but_no_registration_blocks_review(self) -> None:
        """旧版本任务（有 T3 裁决、docs.decisions 为空）升级后必须被拦住，不能静默合并。"""
        self.ask_and_commit()
        doc = self.write_decision_doc()
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root), "--body", "允许",
                                       "--decision-doc", doc]), 0)
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        # 把登记抹掉，模拟升级前就存在的任务（决策在 process 态、库里没有）
        task = self.repo.load_task("T-001")
        task["docs"]["decisions"] = []
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", task)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 1)
        self.assertIn("未登记决策记录", buf.getvalue())

    def test_deleted_decision_doc_blocks_review(self) -> None:
        self.ask_and_commit()
        doc = self.root / "docs" / "decisions" / "2026-11-14-schema.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("# 允许改 schema\n", encoding="utf-8")
        foreman.main(["reply", "T-001", "--repo", str(self.root), "--body", "允许",
                      "--decision-doc", "docs/decisions/2026-11-14-schema.md"])
        doc.unlink()  # 过程态里登记了、库里没了 → 必须拦住 merge
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 1)
        self.assertIn("决策记录文件不存在", buf.getvalue())


class TestDoctor(unittest.TestCase):
    def test_doctor_non_git_repo_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(foreman.main(["doctor", "--repo", td, "--json"]), 1)

    def test_doctor_reports_missing_hcom(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "-q")
            foreman.write_json(root / ".vibe" / "config.json",
                               {"foreman_name": "foreman", "tag": "foreman", "default_agent": "codex",
                                "base": "main", "max_attempts": 3, "require_human_review": False,
                                "hcom_bin": "definitely-not-on-path-xyz"})
            self.assertEqual(foreman.main(["doctor", "--repo", str(root), "--json"]), 1)


if __name__ == "__main__":
    unittest.main()


class TestProtocolParsing(unittest.TestCase):
    """协议头解析与不可解析消息的证据留档（hcom E2E 实测逼出）。"""

    def test_header_allows_inline_body(self):
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import foreman
        # worker 把问题写在协议头同一行（历史行为），必须仍能解析
        got = foreman.header_candidates("[FOREMAN:ASK] task=T-001 dispatch=D-001 范围与目标冲突：xxx")
        self.assertEqual(got, [("ASK", "T-001", "D-001")])
        got2 = foreman.header_candidates("[FOREMAN:DONE] task=T-001 dispatch=D-001\n摘要")
        self.assertEqual(got2, [("DONE", "T-001", "D-001")])

    def test_sender_outside_dispatches_is_flagged(self):
        """协议头权威，但发送者不在派工记录里要留痕：这是名字撞线的残留信号。"""
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import foreman
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = foreman.Repo(Path(tmp))
        cfg = repo.load_or_default_config()
        foreman.write_json(repo.config_path, cfg)
        task = {"id": "T-001", "title": "x", "status": "running", "attempts": 1, "log": [],
                "dispatches": [{"id": "D-001", "worker_handle": "w-T-001"}]}
        foreman.write_json(repo.task_dir("T-001") / "task.json", task)
        res = foreman.apply_protocol_event(repo, cfg, {
            "id": "e1", "instance": "w-other-repo", "data": {
                "text": "[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成"}})
        self.assertEqual(res["sender_unexpected"], ["w-T-001"])
        log = " ".join(x["detail"] for x in repo.load_task("T-001")["log"])
        self.assertIn("疑跨仓串线", log)
        self.assertEqual(res["task_status"], "review", "仍按协议头处理，不做拦截")

    def test_unparsed_message_saved_before_failing(self):
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import foreman
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = foreman.Repo(Path(tmp))
        cfg = repo.load_or_default_config()
        foreman.write_json(repo.config_path, cfg)
        with self.assertRaises(SystemExit) as cm:
            foreman.apply_protocol_event(repo, cfg,
                                         {"id": "e1", "instance": "tina", "data": {"text": "没有协议头"}})
        self.assertNotEqual(cm.exception.code, 0)
        saved = list((repo.tasks_dir / "_unparsed" / "evidence").glob("unparsed-*.md"))
        self.assertEqual(len(saved), 1, "不可解析消息必须先落证据再失败")
        self.assertIn("没有协议头", saved[0].read_text())


class TestScope(unittest.TestCase):
    """--scope 可重复 / 逗号；set-scope 是 T2 扩大范围的落地手段。"""

    def test_parse_scopes_flattens_and_dedupes(self):
        self.assertEqual(foreman.parse_scopes(["src/", "tests/"]), ["src/", "tests/"])
        self.assertEqual(foreman.parse_scopes(["src/, tests/"]), ["src/", "tests/"])
        self.assertEqual(foreman.parse_scopes(["src/", "src/"]), ["src/"])
        self.assertEqual(foreman.parse_scopes(None), [])


class TestSetScope(TempGitRepoMixin):
    def test_repeated_scope_kept_and_set_scope_updates(self) -> None:
        self.assertEqual(foreman.main(["init", "--repo", str(self.root)]), 0)
        self.assertEqual(foreman.main(["task", "new", "--repo", str(self.root), "--title", "t",
                                       "--scope", "src/", "--scope", "tests/,docs/",
                                       "--accept", "true"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["scope"], ["src/", "tests/", "docs/"])
        self.assertEqual(foreman.main(["task", "set-scope", "--repo", str(self.root), "T-001",
                                       "--scope", "lib/"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["scope"], ["lib/"])
        brief = (self.repo.task_dir("T-001") / "brief.md").read_text(encoding="utf-8")
        self.assertIn("- lib/", brief)
        self.assertNotIn("- docs/", brief)

    def test_set_scope_refused_on_merge_ready(self) -> None:
        """review 之后再改范围会让证据失效，必须拒绝（否则 merge 会带着过期审查过闸）。"""
        self.full_task()
        self.dispatch()
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "src" / "f.txt").write_text("x\n", encoding="utf-8")
        self.commit_in(wt, "feat: x")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "merge_ready")
        with self.assertRaises(SystemExit) as cm:
            foreman.main(["task", "set-scope", "--repo", str(self.root), "T-001",
                          "--scope", "docs/"])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self.repo.load_task("T-001")["scope"], ["src/"])


class TestAbandon(TempGitRepoMixin):
    def test_abandon_running_task_allows_redispatch(self) -> None:
        self.full_task()
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\nq")
        self.dispatch()
        self.run_events()
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_answer")
        self.assertEqual(foreman.main(["task", "abandon", "--repo", str(self.root), "T-001",
                                       "--reason", "worker 进程已死"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "failed")
        self.assertIn("worker 进程已死", json.dumps(task["log"], ensure_ascii=False))


class TestOrcaSkippedSurfaced(TempGitRepoMixin):
    """Orca 批次里的非协议消息必须上行提示，不能只报 timeout（E2E 实测教训）。"""

    def test_escalation_batch_yields_skipped_hint(self) -> None:
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        ev = self.root / ".vibe" / "logs" / "fake-orca-event.json"
        ev.write_text(json.dumps({"id": "m1", "type": "escalation", "from": "term_x",
                                 "subject": "Worker stopped without reporting",
                                 "payload": {"taskId": "t1"}}))
        os.environ["FAKE_ORCA_EVENT"] = str(ev)
        self.addCleanup(os.environ.pop, "FAKE_ORCA_EVENT", None)
        transport = foreman.Orca(self.repo, self.repo.config())
        out = transport.wait_message(1)
        self.assertTrue(out.get("timed_out"))
        self.assertEqual([x["type"] for x in out["skipped"]], ["escalation"])
        self.assertIn("Worker stopped without reporting", out["skipped"][0]["subject"])
        log = (self.root / ".vibe" / "logs" / "transport-skipped.log").read_text()
        self.assertIn("escalation", log)


class TestHeaderHygiene(unittest.TestCase):
    """协议头歧义与外部标识拼路径（E2E 后复核发现的缺口）。"""

    def _repo(self) -> tuple[foreman.Repo, Path]:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = foreman.Repo(tmp)
        foreman.write_json(repo.config_path, repo.load_or_default_config())
        return repo, tmp

    def test_ambiguous_headers_fail_with_evidence(self) -> None:
        repo, tmp = self._repo()
        text = ("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完工。\n"
                "转述他任务：\n[FOREMAN:ASK] task=T-002 dispatch=D-002\n要裁决")
        with self.assertRaises(SystemExit) as cm:
            foreman.apply_protocol_event(repo, repo.config(),
                                         {"id": "e", "instance": "w", "data": {"text": text}})
        self.assertNotEqual(cm.exception.code, 0)
        saved = list((repo.tasks_dir / "_unparsed" / "evidence").glob("ambiguous-*.md"))
        self.assertEqual(len(saved), 1, "歧义协议头必须先留档再失败")

    def test_same_header_repeated_is_not_ambiguous(self) -> None:
        repo, tmp = self._repo()
        foreman.write_json(repo.tasks_dir / "T-001" / "task.json", {
            "id": "T-001", "title": "t", "status": "running", "attempts": 1,
            "dispatches": [{"id": "D-001", "worker_handle": "w"}], "asks": [], "gates": [],
            "decisions": [], "log": [], "scope": [], "acceptance": "true"})
        text = "[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成\n[FOREMAN:DONE] task=T-001 dispatch=D-001"
        res = foreman.apply_protocol_event(repo, repo.config(),
                                           {"id": "e", "instance": "w", "data": {"text": text}})
        self.assertEqual(res["type"], "DONE")

    def test_untrusted_sender_cannot_write_outside_evidence_dir(self) -> None:
        repo, tmp = self._repo()
        evil = "../../../../../../tmp/foreman-pwn-probe"
        with self.assertRaises(SystemExit):
            foreman.apply_protocol_event(repo, repo.config(),
                                         {"id": "e", "instance": evil, "data": {"text": "no header"}})
        self.assertFalse(Path("/tmp/foreman-pwn-probe.md").exists())
        base = repo.tasks_dir / "_unparsed" / "evidence"
        files = list(base.glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertNotIn("/", files[0].name)
        self.assertEqual(files[0].resolve().parent, base.resolve())
        stray = [p for p in (repo.tasks_dir / "_unparsed").rglob("*") if p.is_file() and p.parent != base]
        self.assertEqual(stray, [], "证据只能落在 evidence/ 下")


class TestChannelAwareArtifacts(unittest.TestCase):
    """派工单与证据名必须与通道匹配、且不接受外部路径注入。"""

    def test_brief_uses_orca_wording_in_orca_mode(self) -> None:
        task = {"id": "T-1", "title": "x", "scope": [], "acceptance": "true",
                "dispatches": [{"id": "D-1", "run_id": "run_abc"}]}
        orca = foreman.brief_text({"foreman_name": "f", "default_agent": "pi",
                                   "transport": "orca"}, task)
        self.assertIn("run:run_abc", orca)
        self.assertIn("--subject", orca)
        self.assertNotIn("hcom @", orca)
        hcom = foreman.brief_text({"foreman_name": "f", "default_agent": "pi",
                                   "transport": "hcom"}, task)
        self.assertIn("hcom @f", hcom)
        self.assertIn("正文首行", hcom)


class TestEvidencePathInjection(TempGitRepoMixin):
    def test_dispatch_id_cannot_escape_evidence_dir(self) -> None:
        self.full_task()
        self.dispatch()
        evil = "D-001/../../../../../foreman-pwn"
        foreman.apply_protocol_event(self.repo, self.repo.config(),
                                     {"id": "e", "instance": "w",
                                      "data": {"text": f"[FOREMAN:ASK] task=T-001 dispatch={evil}\n问"}})
        self.assertFalse((self.root.parent / "foreman-pwn.md").exists())
        self.assertFalse((self.repo.foreman / "foreman-pwn.md").exists())
        ev = self.repo.task_dir("T-001") / "evidence"
        files = list(ev.glob("ask-*.md"))
        self.assertEqual(len(files), 1, "证据必须落在 evidence/ 内")
        self.assertEqual(files[0].parent, ev)
        self.assertNotIn("/", files[0].name)


class TestAskFromWaitingAnswer(TempGitRepoMixin):
    def test_supervisor_can_escalate_worker_question_to_human(self) -> None:
        """收到 ASK 后若属 T3，主管必须能直接立门（否则只能先替人类答复）。"""
        self.full_task()
        self.dispatch()
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n要不要改 schema？")
        self.run_events()
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_answer")
        self.assertEqual(foreman.main(["ask", "T-001", "--repo", str(self.root),
                                       "--question", "是否允许改 schema？",
                                       "--options", "允许,不允许"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "waiting_human")
        self.assertEqual(task["gates"][-1]["question"], "是否允许改 schema？")
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root),
                                       "--body", "允许",
                                       "--decision-doc", self.write_decision_doc()]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "running")


class TestBatchNotLost(TempGitRepoMixin):
    """check 是消费式的：同批里没被处理的消息必须留档（E2E 后整体 review 发现）。"""

    def test_second_protocol_message_in_batch_is_logged(self) -> None:
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        ev = self.root / ".vibe" / "logs" / "fake-orca-event.json"
        foreman.write_json(ev, [
            {"id": "m1", "type": "status", "from": "t",
             "subject": "[FOREMAN:ASK] task=T-001 dispatch=D-001", "body": "问"},
            {"id": "m2", "type": "worker_done", "from": "t",
             "subject": "[FOREMAN:DONE] task=T-001 dispatch=D-001", "body": "完了"},
        ])
        os.environ["FAKE_ORCA_EVENT"] = str(ev)
        self.addCleanup(os.environ.pop, "FAKE_ORCA_EVENT", None)
        out = foreman.Orca(self.repo, self.repo.config()).wait_message(1)
        self.assertEqual(out["id"], "m1")
        self.assertEqual(out["batch_extra"], ["worker_done"])
        log = (self.root / ".vibe" / "logs" / "batch-unhandled.log").read_text()
        self.assertIn("[FOREMAN:DONE]", log)
        self.assertIn("完了", log)


class TestLogRotation(unittest.TestCase):
    """追加型日志必须轮转，长期运行不能无限增长。"""

    def test_append_log_rotates_over_limit(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "logs" / "x.log"
        foreman.append_log(path, "a" * (foreman.LOG_MAX_BYTES + 10))
        foreman.append_log(path, "tail")
        self.assertTrue(path.with_suffix(".log.1").exists(), "超限必须先轮转")
        self.assertEqual(path.read_text(), "tail\n")


class TestDoctorOrcaRepo(TempGitRepoMixin):
    """doctor 的注册检查必须复用同一次 repo list 结果（否则会白跑一次 120s 预算的命令）。"""

    def _doctor(self) -> dict:
        cfg = self.repo.config()
        cfg["transport"] = "orca"
        foreman.write_json(self.repo.config_path, cfg)
        t = foreman.Orca(self.repo, cfg)
        return {c["name"]: c for c in t.doctor_checks(self.repo, cfg)}

    def test_registered_matches_repo_root(self) -> None:
        os.environ["FAKE_ORCA_REPO_ROOT"] = str(self.root)
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        checks = self._doctor()
        self.assertTrue(checks["orca-daemon"]["ok"])
        self.assertTrue(checks["orca-repo"]["ok"], checks["orca-repo"]["detail"])

    def test_unregistered_repo_is_reported(self) -> None:
        os.environ["FAKE_ORCA_REPO_ROOT"] = "/tmp/somewhere-else"
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        checks = self._doctor()
        self.assertFalse(checks["orca-repo"]["ok"])


class TestDoctorHcomName(TempGitRepoMixin):
    """同机多仓并行：名字被别的仓占用会让 worker 回执串线，doctor 必须拦住。"""

    def _set_list(self, items: list[dict]) -> None:
        os.environ["FAKE_HCOM_LIST"] = json.dumps(items)
        self.addCleanup(os.environ.pop, "FAKE_HCOM_LIST", None)

    def _checks(self, cfg: dict | None = None) -> dict[str, dict]:
        cfg = cfg or self.repo.config()
        return {c["name"]: c for c in foreman.Hcom(self.repo, cfg).doctor_checks(self.repo, cfg)}

    def test_name_owned_by_other_repo_fails(self) -> None:
        name = self.repo.config()["foreman_name"]
        self._set_list([{"name": name, "directory": "/tmp/other-repo"}])
        checks = self._checks()
        self.assertFalse(checks["hcom-name"]["ok"], checks["hcom-name"]["detail"])
        self.assertIn("/tmp/other-repo", checks["hcom-name"]["detail"])
        self.assertIn("--foreman-name", checks["hcom-name"]["fix"])

    def test_sibling_dir_prefix_is_foreign(self) -> None:
        """`/x/api-extra` 前缀相同但不是本仓：doctor 必须报占用。"""
        name = self.repo.config()["foreman_name"]
        self._set_list([{"name": name, "directory": f"{self.root}-extra"}])
        self.assertFalse(self._checks()["hcom-name"]["ok"])

    def test_name_in_this_repo_passes(self) -> None:
        name = self.repo.config()["foreman_name"]
        self._set_list([{"name": name, "directory": str(self.root)}])
        self.assertTrue(self._checks()["hcom-name"]["ok"])

    def test_own_worktree_is_not_foreign(self) -> None:
        name = self.repo.config()["foreman_name"]
        self._set_list([{"name": name, "directory": str(self.root / ".vibe" / "worktrees" / "T-001")}])
        self.assertTrue(self._checks()["hcom-name"]["ok"])

    def test_unparsable_list_fails_visible(self) -> None:
        os.environ["FAKE_HCOM_LIST"] = "not json"
        self.addCleanup(os.environ.pop, "FAKE_HCOM_LIST", None)
        self.assertFalse(self._checks()["hcom-name"]["ok"])

    def test_session_name_mismatch_fails(self) -> None:
        cfg = dict(self.repo.config(), foreman_name="someone-else-foreman")
        foreman.write_json(self.repo.config_path, cfg)
        checks = self._checks(cfg)
        self.assertFalse(checks["hcom-address"]["ok"])
        self.assertIn("someone-else-foreman", checks["hcom-address"]["detail"])
        self.assertIn("init --foreman-name foreman", checks["hcom-address"]["fix"])

    def test_session_name_match_passes(self) -> None:
        name = self.repo.config()["foreman_name"]
        os.environ["FAKE_HCOM_SELF_NAME"] = name
        self.addCleanup(os.environ.pop, "FAKE_HCOM_SELF_NAME", None)
        self.assertTrue(self._checks()["hcom-address"]["ok"])


class TestForemanNamePicking(TempGitRepoMixin):
    """撞名自动加路径短哈希，名字与 tag 成对：同一目录永远同一对，同名目录靠路径区分。"""

    def _set_list(self, items: list[dict] | str) -> None:
        os.environ["FAKE_HCOM_LIST"] = items if isinstance(items, str) else json.dumps(items)
        self.addCleanup(os.environ.pop, "FAKE_HCOM_LIST", None)

    def _slug(self) -> str:
        return foreman.slugify(self.root.name)

    def test_free_name_uses_repo_slug(self) -> None:
        self._set_list([])
        self.assertEqual(foreman.pick_foreman_names(self.repo, str(FIXTURE_HCOM)),
                         (f"{self._slug()}-foreman", self._slug()))

    def test_name_taken_by_other_repo_gets_hash(self) -> None:
        base = f"{self._slug()}-foreman"
        self._set_list([{"name": base, "directory": "/tmp/other-repo"}])
        name, tag = foreman.pick_foreman_names(self.repo, str(FIXTURE_HCOM))
        self.assertRegex(name, r"^.+\-[0-9a-f]{4}\-foreman$")
        self.assertRegex(tag, r"^.+\-[0-9a-f]{4}$", "tag 必须跟着名字一起加哈希，否则 kill tag: 会跨仓")
        self.assertEqual(name, f"{tag}-foreman")

    def test_sibling_dir_prefix_is_not_own(self) -> None:
        """`/x/api-extra` 不是 `/x/api`：前缀相同的兄弟目录也不能算自家。"""
        base = f"{self._slug()}-foreman"
        self._set_list([{"name": base, "directory": f"{self.root}-extra"}])
        self.assertNotEqual(foreman.pick_foreman_names(self.repo, str(FIXTURE_HCOM))[0], base,
                            "兄弟目录占用时仍须避让")

    def test_name_taken_by_own_repo_keeps_plain(self) -> None:
        base = f"{self._slug()}-foreman"
        self._set_list([{"name": base, "directory": str(self.root)}])
        self.assertEqual(foreman.pick_foreman_names(self.repo, str(FIXTURE_HCOM))[0], base)

    def test_own_worktree_counts_as_own(self) -> None:
        base = f"{self._slug()}-foreman"
        self._set_list([{"name": base, "directory": str(self.root / ".vibe" / "worktrees" / "T-001")}])
        self.assertEqual(foreman.pick_foreman_names(self.repo, str(FIXTURE_HCOM))[0], base)

    def test_instances_dict_wrapper_is_parsed(self) -> None:
        base = f"{self._slug()}-foreman"
        self._set_list(json.dumps({"instances": [{"name": base, "directory": "/tmp/other-repo"}]}))
        self.assertNotEqual(foreman.pick_foreman_names(self.repo, str(FIXTURE_HCOM))[0], base)

    def test_missing_hcom_falls_back_to_plain(self) -> None:
        name, tag = foreman.pick_foreman_names(self.repo, "definitely-not-a-real-bin-xyz")
        self.assertEqual((name, tag), (f"{self._slug()}-foreman", self._slug()))

    def test_two_same_named_dirs_differ(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = Path(a) / "api", Path(b) / "api"
            ra.mkdir(), rb.mkdir()
            self.assertNotEqual(foreman.foreman_name_pairs(foreman.Repo(ra))[1],
                                foreman.foreman_name_pairs(foreman.Repo(rb))[1])

    def test_init_picks_hashed_pair_on_collision(self) -> None:
        """新仓库 init 自动避让：假 hcom 放到 PATH 最前，模拟没有自定义 hcom_bin 的首跑。"""
        base = f"{self._slug()}-foreman"
        self._set_list([{"name": base, "directory": "/tmp/other-repo"}])
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{FIXTURE_HCOM.parent}{os.pathsep}{old_path}"
        self.addCleanup(os.environ.__setitem__, "PATH", old_path)
        self.repo.config_path.unlink()
        self.assertEqual(foreman.main(["init", "--repo", str(self.root)]), 0)
        cfg = foreman.json.loads(self.repo.config_path.read_text(encoding="utf-8"))
        self.assertRegex(cfg["foreman_name"], r"^.+\-[0-9a-f]{4}\-foreman$")
        self.assertRegex(cfg["tag"], r"^.+\-[0-9a-f]{4}$")

    def test_init_keeps_existing_name(self) -> None:
        before = foreman.json.loads(self.repo.config_path.read_text(encoding="utf-8"))["foreman_name"]
        self._set_list([{"name": before, "directory": "/tmp/other-repo"}])
        self.assertEqual(foreman.main(["init", "--repo", str(self.root)]), 0)
        after = foreman.json.loads(self.repo.config_path.read_text(encoding="utf-8"))["foreman_name"]
        self.assertEqual(after, before, "已有配置不静默改名")


class TestTagDefaults(TempGitRepoMixin):
    def test_tag_defaults_to_repo_slug(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "api"
            root.mkdir()
            self.assertEqual(foreman.Repo(root).load_or_default_config()["tag"], "api")

    def test_init_can_set_tag(self) -> None:
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--tag", "sb"]), 0)
        self.assertEqual(foreman.json.loads(self.repo.config_path.read_text(encoding="utf-8"))["tag"], "sb")


class TestDefaultForemanName(unittest.TestCase):
    """默认名字带仓库标识：同机两个仓 init 后不再同名。"""

    def test_default_name_carries_repo_slug(self) -> None:
        with tempfile.TemporaryDirectory(prefix="My-Repo-") as td:
            cfg = foreman.Repo(Path(td)).load_or_default_config()
            self.assertNotEqual(cfg["foreman_name"], "foreman")
            self.assertEqual(cfg["foreman_name"], foreman.slugify(Path(td).name) + "-foreman")

    def test_two_repos_get_different_names(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            na = foreman.Repo(Path(a)).load_or_default_config()["foreman_name"]
            nb = foreman.Repo(Path(b)).load_or_default_config()["foreman_name"]
            self.assertNotEqual(na, nb)


class TestMergePostVerify(TempGitRepoMixin):
    """合并后复跑验收不过：合并提交已存在，任务保持 merge_ready，不许自动重试。"""

    def test_merge_keeps_merge_ready_when_post_verify_fails(self) -> None:
        """合并后复跑验收不过：合并提交已存在，任务保持 merge_ready，不许自动重试。"""
        marker = Path(tempfile.mkdtemp()) / "gate"
        self.addCleanup(shutil.rmtree, marker.parent, True)
        self.full_task(accept=f"test -f {marker}")
        self.dispatch()
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(wt, "feat: x")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        marker.touch()  # review 阶段验收通过
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "merge_ready")
        marker.unlink()  # 合并后在主仓库复跑验收会失败

        with self.assertRaises(SystemExit) as cm:
            foreman.main(["merge", "T-001", "--repo", str(self.root)])
        self.assertNotEqual(cm.exception.code, 0, "合并后验收失败必须非零退出")
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "merge_ready", "验收失败不得推进到 completed")
        log = " ".join(x["detail"] for x in task["log"])
        self.assertIn("合并后复跑验收失败", log)
        merged = subprocess.run(["git", "log", "--oneline", "-1"], cwd=self.root,
                                capture_output=True, text=True).stdout
        self.assertIn("Merge foreman/T-001", merged, "合并提交应已存在（人工决定 revert）")



class TestAgentDetection(unittest.TestCase):
    """会话 agent 探测：进程祖先链优先（不会被父会话环境继承污染），环境标记兜底。"""

    def test_comm_hit(self) -> None:
        self.assertEqual(foreman.agent_from_commands(["bash\tbash", "pi\tpi"]), "pi")

    def test_runtime_carrying_cli(self) -> None:
        self.assertEqual(
            foreman.agent_from_commands(
                ["node\tnode /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"]), "claude")

    def test_no_false_positive_on_similar_names(self) -> None:
        self.assertIsNone(foreman.agent_from_commands(
            ["node\tnode /opt/pilot/cli.js", "python3\tpython3 -c pi"]))
        self.assertIsNone(foreman.agent_from_commands(["bash\t/bin/bash -lc ls"]))

    def test_tree_wins_over_inherited_env(self) -> None:
        """pi 里再起 codex 会带着 PI_*；进程树必须压过环境变量。"""
        os.environ["PI_SESSION_ID"] = "sess-parent"
        self.addCleanup(os.environ.pop, "PI_SESSION_ID", None)
        orig = foreman.ancestor_commands
        foreman.ancestor_commands = lambda limit=16: ["python3\tpython3 foreman.py", "codex\tcodex"]
        self.addCleanup(setattr, foreman, "ancestor_commands", orig)
        self.assertEqual(foreman.detect_session_agent(), "codex")

    def test_env_fallback_without_proc(self) -> None:
        os.environ["CODEX_THREAD_ID"] = "t-1"
        self.addCleanup(os.environ.pop, "CODEX_THREAD_ID", None)
        orig = foreman.ancestor_commands
        foreman.ancestor_commands = lambda limit=16: []
        self.addCleanup(setattr, foreman, "ancestor_commands", orig)
        self.assertEqual(foreman.detect_session_agent(), "codex")

    def test_self_argv_is_not_evidence(self) -> None:
        """自己的命令行里有 --agent pi 也不能当作会话身份。"""
        orig = foreman.ancestor_commands
        # 自身命令行里出现 pi 的包路径：如果不跳过自己就会被误判成 pi 会话
        foreman.ancestor_commands = lambda limit=16: ["python3\tpython3 /opt/agents/pi/cli.py --agent pi"]
        self.addCleanup(setattr, foreman, "ancestor_commands", orig)
        self.assertIsNone(foreman.detect_session_agent())


class TestWorkerAgentSelection(TempGitRepoMixin):
    """默认应为「与主管会话同一个 agent」；--agent 与任务登记优先。"""

    def _agent_of(self) -> tuple[str, str]:
        task = self.repo.load_task("T-001")
        d = task["dispatches"][0]
        return d["agent"], d.get("agent_source")

    def test_defaults_to_supervisor_session_agent(self) -> None:
        self.full_task()
        os.environ["FAKE_HCOM_TOOL"] = "pi"             # hcom 身份里 tool=pi（权威）
        self.addCleanup(os.environ.pop, "FAKE_HCOM_TOOL", None)
        self.dispatch()
        self.assertEqual(self._agent_of(), ("pi", "主管会话"))
        self.assertIn("pi", [c[0] for c in self.hcom_calls()], "必须以 pi 拉起 worker")

    def test_explicit_agent_wins(self) -> None:
        self.full_task()
        os.environ["PI_SESSION_ID"] = "sess-1"
        self.dispatch(extra=["--agent", "codex"])
        self.assertEqual(self._agent_of(), ("codex", "命令行 --agent"))

    def test_task_level_agent_wins_over_session(self) -> None:
        self.assertEqual(foreman.main(["init", "--repo", str(self.root)]), 0)
        self.assertEqual(foreman.main(["task", "new", "--repo", str(self.root), "--title", "t",
                                       "--scope", "src/", "--accept", "true",
                                       "--agent", "pi"]), 0)
        os.environ["CODEX_THREAD_ID"] = "thread-1"       # 主管是 codex，但任务指定 pi
        self.dispatch()
        self.assertEqual(self._agent_of(), ("pi", "任务登记"))

    def test_env_marker_used_when_identity_has_no_tool(self) -> None:
        self.full_task()
        os.environ["FAKE_HCOM_TOOL"] = ""               # 身份无 tool（如 ad-hoc 会话）
        os.environ["PI_SESSION_ID"] = "sess-1"
        self.addCleanup(os.environ.pop, "FAKE_HCOM_TOOL", None)
        self.dispatch()
        self.assertEqual(self._agent_of(), ("pi", "主管会话"))

    def test_config_fallback_when_no_session_marker(self) -> None:
        self.full_task()
        os.environ["FAKE_HCOM_TOOL"] = ""
        self.addCleanup(os.environ.pop, "FAKE_HCOM_TOOL", None)
        self.dispatch()
        self.assertEqual(self._agent_of(), ("codex", "config 兜底"))


class TestOrcaRuntimeContext(TempGitRepoMixin):
    """调用时判定：本进程是否跑在 Orca 终端里、是不是 worker（决定能否跑控制面）。"""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        self.full_task()

    def test_coordinator_session_allowed(self) -> None:
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-coordinator"
        os.environ["FAKE_ORCA_WORKER_TERMINAL"] = "term-some-worker"
        ctx = foreman.detect_orca_context(self.repo, self.repo.config())
        self.assertTrue(ctx["in_orca"])
        self.assertIsNone(ctx["worker_dispatch"])
        self.dispatch()  # 主管可以派工

    def test_worker_session_refused(self) -> None:
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-me-worker"
        os.environ["FAKE_ORCA_WORKER_TERMINAL"] = "term-me-worker"
        ctx = foreman.detect_orca_context(self.repo, self.repo.config())
        self.assertEqual(ctx["worker_dispatch"], "ctx-1")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--headless"])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("控制面命令只能在主管会话执行", err.getvalue())

    def test_task_mutations_are_also_guarded(self) -> None:
        """改任务状态同样属控制面：worker 不能给自己 unlock 或改范围。"""
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-me-worker"
        os.environ["FAKE_ORCA_WORKER_TERMINAL"] = "term-me-worker"
        for argv in (["task", "set-scope", "T-001", "--scope", "docs/"],
                     ["task", "set-acceptance", "T-001", "--accept", "true"],
                     ["task", "abandon", "T-001", "--reason", "x"]):
            with self.assertRaises(SystemExit) as cm:
                foreman.main([*argv, "--repo", str(self.root)])
            self.assertNotEqual(cm.exception.code, 0, argv)

    def test_no_orca_context_is_reported(self) -> None:
        ctx = foreman.detect_orca_context(self.repo, self.repo.config())
        self.assertFalse(ctx["in_orca"])
        self.assertIsNone(ctx["worker_dispatch"])


class TestTransportAutoDetection(TempGitRepoMixin):
    """缺省 transport 按运行时判定：Orca 终端→orca，否则 hcom；显式配置优先。"""

    def setUp(self) -> None:
        super().setUp()
        foreman._TRANSPORT_CACHE.clear()
        self.addCleanup(foreman._TRANSPORT_CACHE.clear)

    def _write_cfg(self, **over) -> None:
        cfg = self.repo.config()
        cfg.pop("transport", None)
        cfg.update(over)
        foreman.write_json(self.repo.config_path, cfg)

    def test_auto_picks_orca_inside_orca_terminal(self) -> None:
        self._write_cfg()
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-a"
        self.assertEqual(foreman.resolve_transport(self.repo, self.repo.config()),
                         ("orca", "自动：Orca 终端"))
        self.assertEqual(foreman.effective_config(self.repo)["transport"], "orca")

    def test_auto_picks_hcom_outside_orca(self) -> None:
        self._write_cfg()
        self.assertEqual(foreman.resolve_transport(self.repo, self.repo.config()),
                         ("hcom", "自动：非 Orca 终端"))

    def test_explicit_config_beats_detection(self) -> None:
        self._write_cfg(transport="hcom")
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-a"
        self.assertEqual(foreman.resolve_transport(self.repo, self.repo.config()),
                         ("hcom", "config 显式"))
        cfg = foreman.effective_config(self.repo)
        self.assertEqual(cfg["transport"], "hcom")

    def test_dead_terminal_handle_falls_back_to_hcom(self) -> None:
        self._write_cfg()
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-gone"
        os.environ["FAKE_ORCA_DEAD_TERMINAL"] = "term-gone"
        self.addCleanup(os.environ.pop, "FAKE_ORCA_DEAD_TERMINAL", None)
        self.assertEqual(foreman.resolve_transport(self.repo, self.repo.config())[0], "hcom")

    def test_detection_happens_once_per_process(self) -> None:
        self._write_cfg()
        calls = []
        orig = foreman.detect_orca_context
        foreman.detect_orca_context = lambda repo, cfg: (calls.append(1), orig(repo, cfg))[1]
        self.addCleanup(setattr, foreman, "detect_orca_context", orig)
        foreman.resolve_transport(self.repo, self.repo.config())
        foreman.resolve_transport(self.repo, self.repo.config())
        self.assertEqual(len(calls), 1, "判定结果应缓存，避免每条命令都问 daemon")


class TestContextCaching(TempGitRepoMixin):
    """运行时探测是旁路判断：一条命令里只该问 daemon 一次，且用短超时。"""

    def test_detect_queries_daemon_once_per_repo(self) -> None:
        self.assertEqual(foreman.main(["init", "--repo", str(self.root)]), 0)
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-a"
        foreman.detect_orca_context(self.repo, self.repo.config())
        foreman.detect_orca_context(self.repo, self.repo.config())
        shows = [c for c in self.orca_calls() if c[:2] == ["terminal", "show"]]
        self.assertEqual(len(shows), 1, "重复探测会白跑 daemon 调用")

    def test_detect_timeouts_are_short(self) -> None:
        self.assertEqual(foreman.main(["init", "--repo", str(self.root)]), 0)
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-a"
        foreman.detect_orca_context(self.repo, self.repo.config())
        self.assertLessEqual(foreman.DETECT_TIMEOUT, 20,
                             "探测是旁路：daemon 卡住时不能拖住主命令（原先最长 240s）")

    def test_dead_terminal_short_circuits_run_and_worker_queries(self) -> None:
        """句柄失效时不该再问 Run 与 worker（省两次往返）。"""
        self.assertEqual(foreman.main(["init", "--repo", str(self.root)]), 0)
        os.environ["ORCA_TERMINAL_HANDLE"] = "term-gone"
        os.environ["FAKE_ORCA_DEAD_TERMINAL"] = "term-gone"
        foreman.detect_orca_context(self.repo, self.repo.config())
        cmds = [c[:2] for c in self.orca_calls()]
        self.assertIn(["terminal", "show"], cmds)
        self.assertNotIn(["orchestration", "run-current"], cmds)
        self.assertNotIn(["orchestration", "worker-list"], cmds)

    def test_python314_runtime_is_recognised(self) -> None:
        self.assertTrue(foreman.is_runtime_comm("python3.14"))
        self.assertFalse(foreman.is_runtime_comm("bash"))
        self.assertEqual(foreman.agent_from_commands(
            ["python3.14\tpython3.14 /opt/agents/claude/cli.py"]), "claude")


class TestCommandTimeout(unittest.TestCase):
    """外部命令超时必须降级成 fail-visible 回执，不能抛栈（daemon 卡住时发生过）。"""

    def test_timeout_becomes_exit_124(self) -> None:
        proc = foreman.run_cmd([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
        self.assertEqual(proc.returncode, 124)
        self.assertIn("超时", proc.stderr)

    def test_normal_command_unaffected(self) -> None:
        proc = foreman.run_cmd([sys.executable, "-c", "print('ok')"], timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "ok")
