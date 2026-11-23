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
from unittest.mock import patch

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

    def dispatch2(self, task_id: str) -> None:
        rc = foreman.main(["dispatch", task_id, "--repo", str(self.root), "--headless"])
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
        secrets, skips, allowed = foreman.scan_added_lines(diff)
        self.assertEqual(len(secrets), 1)
        self.assertEqual(len(skips), 1)
        self.assertEqual(allowed, [])

    def test_env_skip_marker_exempts_but_reports(self) -> None:
        """环境前置跳过要自带标记：放行照报，且标记写在被审代码里（随仓库分发、同步抹不掉）。"""
        diff = ('+    pytest.skip("需要 node 18 才能跑冒烟")  # env-skip\n'
                '+    pytest.skip("先跳过，回头补")\n'
                "+git commit --no-verify\n")
        _secrets, skips, allowed = foreman.scan_added_lines(diff)
        self.assertEqual(len(allowed), 1)
        self.assertIn("需要 node", allowed[0])
        self.assertEqual(len(skips), 2, "无标记的跳过与 --no-verify 照拦")

    def test_env_skip_marker_never_exempts_secrets(self) -> None:
        """放行只管跳过；凭据没有「环境依赖」这种正当理由。"""
        diff = "+token = 'supersecretvalue123'  # env-skip\n"
        secrets, _skips, _allowed = foreman.scan_added_lines(diff)
        self.assertEqual(len(secrets), 1)

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
        """非协议消息（heartbeat/status）被 check 消费时必须留痕，不静默丢弃。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = foreman.Repo(root)
            msgs = [{"id": "m-1", "type": "heartbeat", "from": "dispatch:d-1", "subject": "alive"},
                    {"id": "m-2", "type": "status", "from": "dispatch:d-1",
                     "subject": "Worker 正在跑第 2 项"}]
            fake = root / "fake-orca-skip"
            fake.write_text("#!/bin/sh\necho '" + json.dumps({"ok": True, "result": {"messages": msgs}}) + "'\n")
            make_executable(fake)
            o = foreman.Orca(repo, {"foreman_name": "foreman"})
            o.bin = str(fake)
            self.assertTrue(o.wait_message(5).get("timed_out"))
            log = (root / ".vibe" / "logs" / "transport-skipped.log").read_text(encoding="utf-8")
            self.assertIn("heartbeat", log)
            self.assertIn("Worker 正在跑第 2 项", log)

    def test_orca_rejected_delivery_is_surfaced(self) -> None:
        """通道拒收（worker_done 缺 taskId）不能被当成非协议消息跳过，否则主管一直等到超时。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = foreman.Repo(root)
            msgs = [{"id": "m-1", "type": "worker_done", "from": "term_x",
                     "subject": "Rejected worker_done: [FOREMAN:DONE] task=T-001 dispatch=D-001"}]
            fake = root / "fake-orca-rejected"
            fake.write_text("#!/bin/sh\necho '" + json.dumps({"ok": True, "result": {"messages": msgs}}) + "'\n")
            make_executable(fake)
            o = foreman.Orca(repo, {"foreman_name": "foreman"})
            o.bin = str(fake)
            got = o.wait_message(5)
            self.assertNotIn("timed_out", got, "被拒的上报必须显式上报，不能算超时")
            self.assertEqual(got["rejected"]["count"], 1)
            self.assertIn("Rejected worker_done", got["rejected"]["subject"])
            log = (root / ".vibe" / "logs" / "transport-skipped.log").read_text(encoding="utf-8")
            self.assertIn("Rejected worker_done", log)

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

    def test_task_new_reminds_scope_self_check(self) -> None:
        """范围漏项只能在派工前提醒：worker 撞 scope 门要赔一轮往返。"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(foreman.main(["task", "new", "--repo", str(self.root),
                                           "--title", "实现 Y", "--accept", "true"]), 0)
        self.assertIn("范围自检", out.getvalue())

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
        for axis in ("正确性：", "可读性：", "架构：", "安全：", "性能："):
            self.assertIn(axis, brief, f"自审缺{axis[:-1]}轴")
        # 容忍模型输出形态的成本必须留在归一层：消费点各自猜形状是不可逆的沉积
        self.assertIn("外部输入的形态判断只许在归一层", brief)
        self.assertIn("反证一次", brief)
        self.assertIn("仍然全绿，说明缺测试", brief)
        # 大改动必须跑质量工具，否则「装了工具却手搓」没有约束（2026-11-19 实测 doctor 只判存在）
        self.assertIn("第四步：大改动跑质量工具", brief)
        self.assertIn("--numstat", brief)
        self.assertIn("~/tools/crapper/crapper", brief)
        self.assertIn("~/tools/dryer/dryer", brief)
        self.assertIn("不阻断上报", brief)
        # 结果要消费：只贴数字不作处理不算自审完成
        self.assertIn("结果要消费", brief)
        self.assertIn("CRAP > 30", brief)
        self.assertIn("视为自审未完成", brief)
        # 严重度两档：四档里「阻断」与「必须改」同义、「备查」无人消费（2026-11-19 剃刀审计）
        for level in ("必须改", "建议"):
            self.assertIn(level, brief)
        self.assertNotIn("备查", brief)
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


class TestPendingHumanReminder(TempGitRepoMixin):
    """等人拍板的事项每回合重申：处理 worker 交接时不再被会话滚动埋掉（2026-11-15 实测）。"""

    def test_events_appends_pending_human_after_done(self) -> None:
        """处理完 worker 交接后，events 输出尾部必须重申别的任务里等人类的裁决。

        真实事故顺序：T-001 ask 等人拍板，另一任务 DONE 到达，主管处理交接，
        T-001 的提问被会话滚走。状态机禁止 waiting_human → review（裁决前不推进），
        所以场景必然是两个任务。
        """
        self.full_task()
        self.dispatch()
        self.assertEqual(foreman.main(["ask", "T-001", "--repo", str(self.root),
                                       "--question", "允许改 schema？", "--options", "允许,不允许"]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_human")
        # 另一个任务正常走 DONE
        self.assertEqual(foreman.main(["task", "new", "--repo", str(self.root),
                                       "--title", "改文档", "--scope", "docs/",
                                       "--accept", "true"]), 0)
        self.assertEqual(foreman.main(["task", "set-acceptance", "T-002", "--repo",
                                       str(self.root), "--accept", "true"]), 0)
        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "docs" / "note.md").write_text("x\n", encoding="utf-8")
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-q", "-m", "add docs")
        self.dispatch2("T-002")
        self.set_event("[FOREMAN:DONE] task=T-002 dispatch=D-001\n完成")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(foreman.main(["events", "--repo", str(self.root), "--timeout", "1"]), 0)
        text = out.getvalue()
        self.assertIn("等你拍板", text, "events 尾部必须重申待裁决项")
        self.assertIn("允许改 schema？", text)
        self.assertIn("reply <任务>", text)

    def test_escalate_also_reminds(self) -> None:
        """escalation 进来时同样重申待裁决项（三条 emit 路径都要接）。"""
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        self.full_task()
        self.dispatch()  # Orca transport：fake orca 记录并回 worker-start 回执
        self.assertEqual(foreman.main(["ask", "T-001", "--repo", str(self.root),
                                       "--question", "要改 CI 吗？"]), 0)
        # 另一任务派工后，伪造 escalation（worker 结束回合未上报）
        self.assertEqual(foreman.main(["task", "new", "--repo", str(self.root),
                                       "--title", "改文档", "--scope", "docs/",
                                       "--accept", "true"]), 0)
        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "docs" / "note.md").write_text("x\n", encoding="utf-8")
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-q", "-m", "add docs")
        self.dispatch2("T-002")
        t2 = self.repo.load_task("T-002")
        d2 = t2["dispatches"][-1]
        # escalation 的 payload 指向 T-002 的 dispatch：events 会把它转成 [FOREMAN:ESCALATE]
        ev = self.root / ".vibe" / "logs" / "fake-orca-event.json"
        ev.write_text(json.dumps({"id": "m1", "type": "escalation", "from": "term_x",
                                  "subject": "Worker stopped without reporting",
                                  "payload": json.dumps({
                                      "taskId": d2.get("orchestration_task") or "ot-1",
                                      "dispatchId": (d2.get("worker_handle") or "").split(":")[-1],
                                      "stopReason": "finished"})}))
        os.environ["FAKE_ORCA_EVENT"] = str(ev)
        self.addCleanup(os.environ.pop, "FAKE_ORCA_EVENT", None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = foreman.main(["events", "--repo", str(self.root), "--timeout", "1"])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("ESCALATE", text, "escalation 必须被识别并上行")
        self.assertIn("等你拍板", text, "escalation 路径同样要重申待裁决项")
        self.assertIn("要改 CI 吗？", text)

    def test_timeout_path_also_reminds(self) -> None:
        """events 空等超时（无消息）时同样重申待裁决项——这正是"忘了要拍板"最容易发生的场景。"""
        self.full_task()
        self.dispatch()
        self.assertEqual(foreman.main(["ask", "T-001", "--repo", str(self.root),
                                       "--question", "选哪个方案？"]), 0)
        # fake hcom 不注入事件 → 空等超时
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = foreman.main(["events", "--repo", str(self.root), "--timeout", "1"])
        self.assertNotEqual(rc, 0, "空等超时应非零退出（既有行为）")
        text = out.getvalue()
        self.assertIn("等你拍板", text, "超时路径同样要重申待裁决项")
        self.assertIn("选哪个方案？", text)

    def test_pending_cleared_after_reply(self) -> None:
        self.full_task()
        self.dispatch()
        foreman.main(["ask", "T-001", "--repo", str(self.root), "--question", "选哪个？"])
        rel = self.write_decision_doc("2026-11-15-test-decision.md")
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root),
                                       "--body", "选 A", "--decision-doc", rel]), 0)
        self.assertEqual(foreman.pending_human_decisions(self.repo), [],
                         "裁决后不得再出现在提醒里")

    def test_waiting_answer_not_counted(self) -> None:
        """waiting_answer 是等 worker 回话，不该出现在等人拍板的清单里。"""
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        task["status"] = "waiting_answer"
        self.repo.save_task(task)
        self.assertEqual(foreman.pending_human_decisions(self.repo), [])

    def test_waited_minutes_shown(self) -> None:
        self.full_task()
        self.dispatch()
        foreman.main(["ask", "T-001", "--repo", str(self.root), "--question", "需要改 CI？"])
        task = self.repo.load_task("T-001")
        gate = task["gates"][-1]
        gate["at"] = "2026-01-01T00:00:00Z"  # 很久以前：等了 N 分钟要显示出来
        self.repo.save_task(task)
        items = foreman.pending_human_decisions(self.repo)
        self.assertEqual(len(items), 1)
        self.assertGreater(items[0]["waited_min"], 10000)
        self.assertIn("已等", foreman.human_decisions_line(items))


class TestDispatchReflection(TempGitRepoMixin):
    """派发反思（2026-11-15 storyboard master 会话）：重派结算、scope 外路径提醒、人工解锁。"""

    def test_retry_stops_previous_worker(self) -> None:
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        task["status"] = "failed"
        self.repo.save_task(task)
        calls_before = len(self.hcom_calls())
        self.dispatch(["--retry-note", "缩 scope 到 tests/"])
        calls = self.hcom_calls()[calls_before:]
        self.assertTrue(any(c[0] == "kill" and c[1] == "w-T-001" for c in calls),
                        "重派前必须停掉上一 attempt 的 worker，否则僵尸会话占树并混淆协议消息")
        task = self.repo.load_task("T-001")
        self.assertEqual(task["dispatches"][-1]["id"], "D-002")
        launch = next(c for c in calls if c[0] == "codex")
        self.assertEqual(launch[launch.index("--name") + 1], "w-T-001-a2",
                         "重派不复用旧名")

    def test_retry_note_warns_scope_outside_path(self) -> None:
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        task["status"] = "failed"
        self.repo.save_task(task)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--headless",
                               "--retry-note", "顺便改 pipeline/other.py 的调用方"])
        self.assertEqual(rc, 0, "提醒不阻断重派")
        self.assertIn("scope 外路径", out.getvalue())
        self.assertIn("pipeline/other.py", out.getvalue())

    def test_retry_note_no_warning_when_path_in_scope(self) -> None:
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        task["status"] = "failed"
        self.repo.save_task(task)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--headless",
                          "--retry-note", "继续改 src/keep.txt"])
        self.assertNotIn("scope 外路径", out.getvalue())

    def test_unblock_moves_blocked_to_failed(self) -> None:
        self.full_task()
        task = self.repo.load_task("T-001")
        task["status"] = "blocked"
        self.repo.save_task(task)
        rc = foreman.main(["task", "unblock", "T-001", "--repo", str(self.root),
                           "--reason", "验收环境缺陷已修"])
        self.assertEqual(rc, 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "failed")
        notes = [x["detail"] for x in task["log"]]
        self.assertEqual(sum("验收环境缺陷已修" in n for n in notes), 1, "解锁原因只记一次")
        self.assertEqual(task["attempts"], 0, "默认不重置计数")

    def test_unblock_reset_attempts(self) -> None:
        self.full_task()
        task = self.repo.load_task("T-001")
        task["status"] = "blocked"
        task["attempts"] = 3
        self.repo.save_task(task)
        rc = foreman.main(["task", "unblock", "T-001", "--repo", str(self.root),
                           "--reason", "根因已判明", "--reset-attempts"])
        self.assertEqual(rc, 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["attempts"], 0)

    def test_unblock_rejects_non_blocked(self) -> None:
        self.full_task()
        with self.assertRaises(SystemExit):
            foreman.main(["task", "unblock", "T-001", "--repo", str(self.root), "--reason", "x"])


class TestWorktreeBootstrap(TempGitRepoMixin):
    """隔离的代价要配套自举：新树缺 .env/.venv 会让第一次验收假红（实测三次手工软链）。"""

    def setUp(self) -> None:
        super().setUp()
        self.full_task()  # 只建任务，不派工：派工本身是被测对象

    def test_worktree_links_symlink_registered_files(self) -> None:
        """隔离的代价要配套自举：新树缺 .env/.venv 会让第一次验收假红。"""
        (self.root / ".env").write_text("TOKEN=1\n", encoding="utf-8")
        cfg = self.repo.config()
        cfg["worktree_links"] = [".env"]
        self.repo.config_path.write_text(json.dumps(cfg), encoding="utf-8")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.dispatch()
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        self.assertTrue((wt / ".env").is_symlink(), "登记的文件必须软链进新树")
        self.assertEqual((wt / ".env").read_text(encoding="utf-8"), "TOKEN=1\n")
        self.assertIn("已把主仓的 .env 软链进新树", out.getvalue())

    def test_worktree_links_refuse_paths_outside_repo(self) -> None:
        """登记项不许跳出仓库：软链到仓外的 .env 等于把别的目录带进树。"""
        cfg = self.repo.config()
        cfg["worktree_links"] = ["../outside.env", "/etc/passwd"]
        self.repo.config_path.write_text(json.dumps(cfg), encoding="utf-8")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.dispatch()
        self.assertIn("含非法路径", out.getvalue())
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        self.assertFalse((wt / "outside.env").exists())
        self.assertFalse((wt / "etc" / "passwd").exists())

    def test_worktree_links_report_missing_and_not_ignored(self) -> None:
        """登记了主仓没有的文件、或软链没被忽略，都要当场说出来，不静默。"""
        (self.root / "notes.txt").write_text("x\n", encoding="utf-8")  # 未提交也未忽略
        cfg = self.repo.config()
        cfg["worktree_links"] = [".env", "notes.txt"]  # 前者不存在，后者没被忽略
        self.repo.config_path.write_text(json.dumps(cfg), encoding="utf-8")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.dispatch()
        text = out.getvalue()
        self.assertIn("登记了但主仓不存在：.env", text)
        self.assertIn("软链未被忽略", text)
        self.assertIn("notes.txt", text)

class TestOrcaWorktreeLinks(TempGitRepoMixin):
    """Orca 的树由 worker-start 建，路径只在回执里，只能启动后补链。"""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        self.full_task()
        self.wt = Path(tempfile.mkdtemp(prefix="orca-wt-")) / "wt"
        run_git(self.root, "worktree", "add", str(self.wt), "-b", "foreman/T-001-impl", "master")
        os.environ["FAKE_ORCA_WT"] = str(self.wt)
        (self.root / ".env").write_text("TOKEN=1\n", encoding="utf-8")
        cfg = self.repo.config()
        cfg["worktree_links"] = [".env"]
        self.repo.config_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(foreman.main(["dispatch", "T-001", "--repo", str(self.root)]), 0)

    def test_links_into_orca_managed_tree(self) -> None:
        self.assertTrue((self.wt / ".env").is_symlink(), "Orca 托管树也要拿到登记的本地配置")
        self.assertEqual((self.wt / ".env").read_text(encoding="utf-8"), "TOKEN=1\n")


class TestDispatch(TempGitRepoMixin):
    def test_dispatch_creates_tree_and_calls_hcom(self) -> None:
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["attempts"], 1)
        d = task["dispatches"][0]
        self.assertEqual(d["id"], "D-001")
        # worker_handle 记的是通道返回的实际实例名（假 hcom 固定返回 w-T-001）；
        # 传给 --name 的是带 attempt 的名字，重派不复用旧名（否则新旧会话混淆协议消息）
        self.assertEqual(d["worker_handle"], "w-T-001")
        self.assertEqual(d["branch"], "foreman/T-001-实现-x")
        self.assertTrue(Path(d["worktree"]).exists())
        launch = next(c for c in self.hcom_calls() if c[0] == "codex")
        self.assertEqual(launch[launch.index("--name") + 1], "w-T-001-a1")
        self.assertIn("--dir", launch)
        self.assertIn("--headless", launch)
        prompt = launch[launch.index("--hcom-prompt") + 1]
        self.assertIn("FOREMAN:ASK", prompt)
        self.assertIn("D-001", prompt)
        self.assertIn("brief.md", prompt)
        self.assertIn("发 DONE 之前先自审", prompt)
        # 自审细节只归 brief 一处：prompt 留指针，否则同一份要求双份注入且会漂移
        self.assertIn("按派工单「自审」节的步骤做", prompt)
        self.assertIn("五轴", prompt)
        self.assertIn("反证", prompt)

    def test_infra_dispatch_does_not_burn_attempts(self) -> None:
        """主管侧缺陷（验收命令写错/缺环境/scope 漏登记）重跑不占尝试次数。

        实测：T-027 派了 4 次，前三次全是主管侧问题，配额烧光后任务差点被判
        「该拆」，而代码方向从未被否。
        """
        self.full_task()
        self.dispatch()
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        foreman.main(["reject", "T-001", "--repo", str(self.root), "--note", "缩 scope"])
        self.dispatch(["--retry-note", "缩 scope"])
        task = self.repo.load_task("T-001")
        self.assertEqual(task["attempts"], 2)

        # 再一次审查不通过 → failed，此时用 infra 重跑
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-002\n完成")
        self.run_events()
        foreman.main(["reject", "T-001", "--repo", str(self.root), "--note", "验收命令写错，重跑"])
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed")

        self.dispatch(["--infra", "验收命令的 && 链没包 shell"])
        task = self.repo.load_task("T-001")
        self.assertEqual(task["attempts"], 2, "infra 重跑不推进尝试次数")
        self.assertEqual(len(task["dispatches"]), 3)
        self.assertTrue(task["dispatches"][-1].get("infra"))
        self.assertEqual(task["dispatches"][-1]["attempt"], 2, "infra 派工记录的 attempt 不推进")
        brief = (self.repo.task_dir("T-001") / "brief.md").read_text(encoding="utf-8")
        self.assertIn("主管侧修正重跑，不计入尝试次数", brief)
        self.assertIn("&& 链没包 shell", brief)

    def _failed_with_prev(self, attempts: int = 1) -> None:
        """把任务摆成「已派过 attempts 次、审查不通过」的 failed 态。"""
        task = self.repo.load_task("T-001")
        task["attempts"] = attempts
        task["status"] = "failed"
        task["dispatches"] = [{"id": f"D-00{i}", "attempt": i, "at": foreman.now_iso(),
                               "worktree": str(self.root / ".vibe" / "worktrees" / "T-001")}
                              for i in range(1, attempts + 1)]
        self.repo.save_task(task)

    def test_infra_dispatch_requires_reason_and_failed_state(self) -> None:
        self.full_task()
        # 首次派工不能标 infra
        with self.assertRaises(SystemExit):
            foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--infra", "x"])
        # 状态不是 failed 不能标 infra（ready 也不行）
        with self.assertRaises(SystemExit):
            foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--infra", "x"])
        # 理由空白拒绝
        self._failed_with_prev()
        with self.assertRaises(SystemExit):
            foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--infra", "   "])
        # 与 --retry-note 同时给拒绝（两个来源会互相盖）
        with self.assertRaises(SystemExit):
            foreman.main(["dispatch", "T-001", "--repo", str(self.root),
                          "--infra", "x", "--retry-note", "y"])

    def test_infra_dispatch_works_after_attempts_exhausted(self) -> None:
        """配额用满、人工 unblock 判明是主管侧缺陷后，infra 重跑不推进次数。"""
        self.full_task()
        max_attempts = foreman.effective_config(self.repo).get("max_attempts", 3)
        self._failed_with_prev(attempts=max_attempts)
        with self.assertRaises(SystemExit):  # 普通重派被配额挡住
            foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--retry-note", "x"])
        self.assertEqual(self.repo.load_task("T-001")["status"], "blocked")
        # blocked 仍是人类门：人工判明根因后 unblock（不重置次数），再 infra 重跑
        foreman.main(["task", "unblock", "T-001", "--repo", str(self.root),
                      "--reason", "前三次失败都是主管侧缺陷"])
        self.assertEqual(foreman.main(["dispatch", "T-001", "--repo", str(self.root),
                                       "--infra", "缺 .env 导致验收假红"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["attempts"], max_attempts, "infra 不推进次数")
        self.assertTrue(task["dispatches"][-1].get("infra"))

    def test_brief_attempt_number_matches_attempts(self) -> None:
        """派工单的「第 N 次尝试」必须等于 attempts，原先写 attempts+1 会多算一次。"""
        task = {"id": "T-1", "title": "x", "scope": [], "acceptance": "true", "attempts": 2,
                "dispatches": [{"id": "D-1", "attempt": 2}],
                "retry_notes": [{"at": "now", "note": "缩 scope"}]}
        brief = foreman.brief_text({"foreman_name": "f", "base": "main", "transport": "hcom"}, task)
        self.assertIn("第 2 次尝试", brief)
        self.assertNotIn("第 3 次尝试", brief)

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
        # 自动项之外必须给出人工复核口径：五轴、反证、严重度
        items = " ".join(task["review"]["manual_items"])
        for kw in ("五轴读 diff", "反证一次", "严重度", "逐个提交核对"):
            self.assertIn(kw, items)

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

    def test_report_refuses_when_not_completed(self) -> None:
        """竣工单是归档后的事实源：未合并就写进 docs/progress/ 会被当成已完成。"""
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "review")
        with self.assertRaises(SystemExit) as cm:
            foreman.main(["report", "T-001", "--repo", str(self.root)])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertFalse(list((self.root / "docs" / "progress").glob("*-T-001.md")),
                         "未 completed 不应落竣工单")

    def test_review_after_failed_runs_again(self) -> None:
        """审查不通过落到 failed 后必须能再审：状态机的边要有入口，否则只能手改 task.json。"""
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "README.md").write_text("越界\n", encoding="utf-8")
        self.commit_in(wt, "oos")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        foreman.main(["review", "T-001", "--repo", str(self.root)])
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed")

        # 修完（这里用扩 scope 模拟）直接再审，不必重派
        self.assertEqual(foreman.main(["task", "set-scope", "T-001", "--repo", str(self.root),
                                       "--scope", "src/", "--scope", "README.md"]), 0)
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root), "--json"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "merge_ready")
        self.assertTrue(any("再审一次" in x["detail"] for x in task["log"]))

    def test_reply_marks_ask_answered(self) -> None:
        """asks[] 记「问过」也要记「答过」：只写不答的账，人读 task.json 会以为还在等。"""
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n用哪个库？")
        self.run_events()
        self.assertFalse(self.repo.load_task("T-001")["asks"][-1].get("answered_at"))
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root),
                                       "--body", "选 A"]), 0)
        ask = self.repo.load_task("T-001")["asks"][-1]
        self.assertTrue(ask.get("answered_at"), "答复后 asks[] 必须留答复时间")
        self.assertEqual(ask.get("answer"), "选 A")

    def test_reject_after_review_passes(self) -> None:
        """五轴是在 review 报全绿之后读的：那时状态已是 merge_ready，打回必须走得通。"""
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(wt, "feat: x")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "merge_ready")

        self.assertEqual(foreman.main(["reject", "T-001", "--repo", str(self.root),
                                       "--note", "判据写了两份，收成一处"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "failed")
        self.assertTrue(any("判据写了两份，收成一处" in x["detail"] for x in task["log"]),
                        "裁决原文必须进事件日志，否则重派时无据可依")

        # failed 可直接重派，retry-note 把要改什么交给下一次 attempt
        self.dispatch(["--retry-note", "判据收成一处"])
        self.assertEqual(self.repo.load_task("T-001")["status"], "running")

    def test_reject_accepts_body_alias_and_failed_state(self) -> None:
        """--body 是 --note 的别名；review 已落 failed 时仍要能补记裁决原文。"""
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        self.assertEqual(foreman.main(["reject", "T-001", "--repo", str(self.root),
                                       "--body", "判据重复"]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "failed")
        self.assertTrue(any("判据重复" in x["detail"] for x in task["log"]))
        # 已经是 failed 时再记一次不报错（不重复迁移，只补日志）
        self.assertEqual(foreman.main(["reject", "T-001", "--repo", str(self.root),
                                       "--note", "再补一条"]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed")
        self.assertTrue(any("再补一条" in x["detail"] for x in self.repo.load_task("T-001")["log"]))

    def test_brief_names_previous_branch_when_tree_changes(self) -> None:
        """换树重派时新树是空的：不点名上一分支，worker 会以为自己在做同一件事的第三次。"""
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "README.md").write_text("越界\n", encoding="utf-8")
        self.commit_in(wt, "oos")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        foreman.main(["review", "T-001", "--repo", str(self.root)])
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed")

        # 模拟 Orca 复用旧树失败：把上一 attempt 的树路径改成一个不存在的新路径
        task = self.repo.load_task("T-001")
        prev_branch = task["dispatches"][-1]["branch"]
        task["dispatches"][-1]["worktree"] = str(self.root / ".vibe" / "worktrees" / "gone")
        self.repo.save_task(task)
        self.dispatch(["--retry-note", "缩 scope 到 src/"])
        brief = (self.repo.task_dir("T-001") / "brief.md").read_text(encoding="utf-8")
        self.assertIn("上一 attempt 的成果不在你这棵树里", brief)
        self.assertIn(prev_branch, brief)
        self.assertIn("cherry-pick", brief)

    def test_reject_requires_note(self) -> None:
        """没有裁决原文的驳回等于没驳回：重派时无据可依。"""
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        self.assertEqual(self.repo.load_task("T-001")["status"], "review")
        with self.assertRaises(SystemExit):
            foreman.main(["reject", "T-001", "--repo", str(self.root), "--note", "   "])
        self.assertEqual(self.repo.load_task("T-001")["status"], "review", "缺原文不得改状态")

    def test_reject_refuses_outside_review(self) -> None:
        """worker 还在干活时不能驳回：那时没有可审的产物。"""
        with self.assertRaises(SystemExit):
            foreman.main(["reject", "T-001", "--repo", str(self.root), "--note", "x"])
        self.assertEqual(self.repo.load_task("T-001")["status"], "running")

    def test_reject_from_review_before_running_review(self) -> None:
        """读完 diff 再跑 review 的次序也要支持：review 态能打回。"""
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        self.assertEqual(self.repo.load_task("T-001")["status"], "review")
        self.assertEqual(foreman.main(["reject", "T-001", "--repo", str(self.root),
                                       "--note", "结构问题"]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed")

    def test_reply_marks_ask_of_that_dispatch(self) -> None:
        """迟到的答复不能记到别的 attempt 的请示上：按 dispatch 配对。"""
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n第一次请示")
        self.run_events()
        # 第二次派工后又有一次请示，此时回第一次的 dispatch
        task = self.repo.load_task("T-001")
        task["asks"].append({"dispatch": "D-002", "from": "w-T-001-a2", "at": "2026-01-01T00:00:00Z",
                             "question": "第二次请示"})
        self.repo.save_task(task)
        self.assertEqual(foreman.main(["reply", "T-001", "--repo", str(self.root),
                                       "--dispatch", "D-001", "--body", "答第一次"]), 0)
        asks = self.repo.load_task("T-001")["asks"]
        self.assertTrue(asks[0].get("answered_at"), "D-001 的请示要记答复")
        self.assertFalse(asks[1].get("answered_at"), "D-002 的请示不能被记上别人的答复")

    def test_brief_requires_actionable_leftovers(self) -> None:
        """遗留项写成可直接派单的形态：只写「后续优化 XX」等于没交代。"""
        brief = (self.repo.task_dir("T-001") / "brief.md").read_text(encoding="utf-8")
        self.assertIn("遗留项写成可直接派单的形态", brief)
        self.assertIn("改哪、验收命令是什么", brief)

    def test_retry_without_note_is_refused(self) -> None:
        """重派必须写清这次改了什么：原样重派只会再失败一次。"""
        wt = Path(self.repo.load_task("T-001")["dispatches"][0]["worktree"])
        (wt / "README.md").write_text("越界\n", encoding="utf-8")
        self.commit_in(wt, "oos")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.run_events()
        foreman.main(["review", "T-001", "--repo", str(self.root)])
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed")
        with self.assertRaises(SystemExit) as cm:
            foreman.main(["dispatch", "T-001", "--repo", str(self.root), "--headless"])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "failed", "被拒的派工不能改状态")

        self.dispatch(["--retry-note", "缩 scope 到 src/keep.txt"])
        task = self.repo.load_task("T-001")
        self.assertEqual(task["retry_notes"][-1]["note"], "缩 scope 到 src/keep.txt")
        brief = (self.root / ".vibe" / "tasks" / "T-001" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("重派说明", brief, "worker 必须看到这次改了什么")
        self.assertIn("缩 scope 到 src/keep.txt", brief)

    def test_dispatch_refuses_unmet_dependency(self) -> None:
        """depends_on 是门槛不是调度：前置没 completed 就不许派工。"""
        rc = foreman.main(["task", "new", "--repo", str(self.root), "--title", "依赖前置",
                           "--scope", "docs/", "--accept", "true", "--depends-on", "T-001"])
        self.assertEqual(rc, 0)
        with self.assertRaises(SystemExit) as cm:
            foreman.main(["dispatch", "T-002", "--repo", str(self.root), "--headless"])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self.repo.load_task("T-002")["status"], "ready")

    def test_dispatch_refuses_scope_overlap_with_in_flight(self) -> None:
        """两个在跑任务改同一批文件必撞合并冲突：scope 相交默认拒绝，显式放行才派。"""
        rc = foreman.main(["task", "new", "--repo", str(self.root), "--title", "同目录并行",
                           "--scope", "src/api/", "--accept", "true"])
        self.assertEqual(rc, 0)
        with self.assertRaises(SystemExit) as cm:
            foreman.main(["dispatch", "T-002", "--repo", str(self.root), "--headless"])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self.repo.load_task("T-002")["status"], "ready")
        # 显式放行后可以派
        self.assertEqual(foreman.main(["dispatch", "T-002", "--repo", str(self.root),
                                       "--headless", "--allow-overlap"]), 0)
        self.assertIn(self.repo.load_task("T-002")["status"], ("dispatched", "running"))

    def test_disjoint_scope_dispatches_in_parallel(self) -> None:
        """scope 不相交就该放行：策略不是禁止并行。"""
        rc = foreman.main(["task", "new", "--repo", str(self.root), "--title", "另一个目录",
                           "--scope", "docs/", "--accept", "true"])
        self.assertEqual(rc, 0)
        self.assertEqual(foreman.main(["dispatch", "T-002", "--repo", str(self.root), "--headless"]), 0)
        self.assertIn(self.repo.load_task("T-002")["status"], ("dispatched", "running"))

    def _batch_task(self, *accepts: str) -> None:
        args = ["task", "new", "--repo", str(self.root), "--title", "批次两件", "--scope", "src/"]
        for i, cmd in enumerate(accepts, start=1):
            args += ["--item", f"第 {i} 件", "--accept", cmd]
        self.assertEqual(foreman.main(args), 0)

    def _dispatch_batch(self) -> Path:
        self.assertEqual(foreman.main(["dispatch", "T-002", "--repo", str(self.root),
                                       "--headless", "--allow-overlap"]), 0)
        wt = Path(self.repo.load_task("T-002")["dispatches"][0]["worktree"])
        (wt / "src" / "batch.txt").write_text("x\n", encoding="utf-8")
        self.commit_in(wt, "feat: batch")
        self.set_event("[FOREMAN:DONE] task=T-002 dispatch=D-001\n完成")
        self.run_events()
        return wt

    def test_batch_task_runs_each_item(self) -> None:
        """一串有依赖的子项当一个任务派出去：逐项验收、逐项留证，主管只审一次。"""
        self._batch_task("true", "true")
        task = self.repo.load_task("T-002")
        self.assertEqual([it["title"] for it in task["items"]], ["第 1 件", "第 2 件"])
        brief = (self.root / ".vibe" / "tasks" / "T-002" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("## 子项", brief)
        self.assertIn("第 1 件", brief)
        self.assertIn("每项一个 commit", brief)

        self._dispatch_batch()
        self.assertEqual(foreman.main(["review", "T-002", "--repo", str(self.root)]), 0)
        self.assertEqual(self.repo.load_task("T-002")["status"], "merge_ready")
        ev = self.repo.task_dir("T-002") / "evidence"
        self.assertTrue((ev / "acceptance-D-001-01.log").exists(), "每个子项各一份证据")
        self.assertTrue((ev / "acceptance-D-001-02.log").exists())
        checks = {c["name"]: c["ok"] for c in self.repo.load_task("T-002")["review"]["checks"]}
        self.assertTrue(checks["acceptance:1 第 1 件"])
        self.assertTrue(checks["acceptance:2 第 2 件"])

    def test_batch_task_fails_and_names_the_item(self) -> None:
        """子项验收失败要指名第几项，不能只说"整批失败"。"""
        self._batch_task("true", "false")
        self._dispatch_batch()
        self.assertEqual(foreman.main(["review", "T-002", "--repo", str(self.root)]), 1)
        task = self.repo.load_task("T-002")
        self.assertEqual(task["status"], "failed")
        failed = [c["name"] for c in task["review"]["checks"] if not c["ok"]]
        self.assertEqual(failed, ["acceptance:2 第 2 件"])
        ev = self.repo.task_dir("T-002") / "evidence"
        self.assertTrue((ev / "acceptance-D-001-02.log").exists(), "失败项也要留证")

    def test_item_accept_count_mismatch_refused(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            foreman.main(["task", "new", "--repo", str(self.root), "--title", "配对错",
                          "--scope", "src/", "--item", "a", "--item", "b", "--accept", "true"])
        self.assertNotEqual(cm.exception.code, 0)

    def _set_unread(self, unread: int) -> None:
        name = self.repo.config()["foreman_name"]
        os.environ["FAKE_HCOM_LIST"] = json.dumps([{"name": name, "unread_count": unread}])

    def test_orca_has_pending_uses_peek(self) -> None:
        """Orca 侧待收探测走 check --peek（只读不标记），有消息 True、空批 False。"""
        cfg = dict(self.repo.config(), transport="orca")
        transport = foreman.Orca(self.repo, cfg)
        os.environ.pop("FAKE_ORCA_EVENT", None)
        self.assertFalse(transport.has_pending())
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n用哪个库？")
        self.assertTrue(transport.has_pending())
        self.assertTrue(transport.has_pending(), "peek 不该把消息标记成已读")

    def test_events_refuses_third_consecutive_empty_wait(self) -> None:
        """连续两轮空等后不再开窗口：规则从"建议"变成机械拦截。

        实测来源：主管把 events 当轮询连环开了三次（420s / 540s / 540s），
        文档只写"最多两轮"却没定义"轮"，也没有任何拦截。
        """
        os.environ.pop("FAKE_HCOM_EVENT", None)
        os.environ.pop("FAKE_ORCA_EVENT", None)
        self._set_unread(0)
        for i in (1, 2):
            self.assertEqual(self.run_events(), 1)
            self.assertEqual(foreman.read_wait_streak(self.repo), i)

        with self.assertRaises(SystemExit) as cm:
            foreman.main(["events", "--repo", str(self.root), "--timeout", "5"])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(foreman.read_wait_streak(self.repo), 2, "被拒的那次不该再计数")


    def test_refusal_tells_you_to_end_the_turn(self) -> None:
        """拒绝文案是这条守卫唯一的价值载体：必须指明结束回合 + 写 handoff。"""
        os.environ.pop("FAKE_HCOM_EVENT", None)
        os.environ.pop("FAKE_ORCA_EVENT", None)
        self._set_unread(0)
        for _ in (1, 2):
            self.assertEqual(self.run_events(), 1)
        buf, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
            with self.assertRaises(SystemExit):
                foreman.main(["events", "--repo", str(self.root), "--timeout", "5"])
        msg = buf.getvalue() + errbuf.getvalue()
        self.assertIn("结束这个回合", msg)
        self.assertIn("handoff.md", msg)
        self.assertIn("worker-read", msg, "要给出别继续等时的替代动作")

    def test_stale_streak_does_not_block_a_new_session(self) -> None:
        """上一个会话留下的计数不该拦住这个会话的第一次等待（保鲜期 30 分钟）。"""
        os.environ.pop("FAKE_HCOM_EVENT", None)
        os.environ.pop("FAKE_ORCA_EVENT", None)
        self._set_unread(0)
        foreman.write_json(foreman.wait_streak_path(self.repo),
                           {"empty_waits": 2, "at": "2026-11-01T00:00:00Z"})
        self.assertEqual(foreman.read_wait_streak(self.repo), 0, "隔了 12 天不再算连续")
        self.assertEqual(self.run_events(), 1, "不该被拒绝，应该正常等一轮再超时")

    def test_unknown_pending_warns_but_does_not_block(self) -> None:
        """通道探测不了待收消息时只警告：宁可少拦一次，也不能把主管锁死。"""
        os.environ.pop("FAKE_HCOM_EVENT", None)
        os.environ.pop("FAKE_ORCA_EVENT", None)
        os.environ["FAKE_HCOM_LIST"] = "[]"  # 找不到本实例 → has_pending 返回 None
        for _ in (1, 2):
            self.assertEqual(self.run_events(), 1)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = foreman.main(["events", "--repo", str(self.root), "--timeout", "5"])
        self.assertEqual(rc, 1, "应正常超时退出，而不是被拒绝")
        self.assertIn("无法无损探测", buf.getvalue())

    def test_pending_message_is_consumed_even_at_the_cap(self) -> None:
        """已经到上限时，若确有消息在等就必须放行——否则主管被永久锁在门外。"""
        os.environ.pop("FAKE_ORCA_EVENT", None)
        self._set_unread(0)
        os.environ.pop("FAKE_HCOM_EVENT", None)
        for _ in (1, 2):
            self.assertEqual(self.run_events(), 1)
        self._set_unread(1)
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n用哪个库？选项 A/B")
        self.assertEqual(self.run_events(), 0, "有消息时必须能消费")
        self.assertEqual(foreman.read_wait_streak(self.repo), 0, "消费后清零")
        self._set_unread(0)
        os.environ.pop("FAKE_HCOM_EVENT", None)
        self.assertEqual(self.run_events(), 1, "清零后允许重新等待")

    def test_empty_wait_output_tells_you_to_end_the_turn(self) -> None:
        """超时输出必须给出下一步动作，而不只是说"超时"。"""
        os.environ.pop("FAKE_HCOM_EVENT", None)
        os.environ.pop("FAKE_ORCA_EVENT", None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            foreman.main(["events", "--repo", str(self.root), "--timeout", "5"])
        out = buf.getvalue()
        self.assertIn("第 1/" + str(foreman.MAX_EMPTY_WAITS) + " 轮空等", out)
        self.assertIn("结束回合", out)
        self.assertIn("handoff.md", out)

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

        # 第 2 次：重派复用既有树，再次失败（重派必须写清这次改了什么）
        self.dispatch(["--retry-note", "缩 scope 到 src/keep.txt"])
        task = self.repo.load_task("T-001")
        self.assertEqual(task["attempts"], 2)
        wt = Path(task["dispatches"][-1]["worktree"])
        (wt / "README.md").write_text("又越界\n", encoding="utf-8")
        self.commit_in(wt, "oos2")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-002\n完成")
        self.run_events()
        foreman.main(["review", "T-001", "--repo", str(self.root)])

        # 第 3 次失败 → blocked，且不得再派
        self.dispatch(["--retry-note", "补背景：README 不在范围内，别碰"])
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

    def test_done_after_ask_moves_to_review(self) -> None:
        """答复可能不经 reply（主管直接发通道消息），worker 恢复后直接交 DONE：不能拒收。"""
        self.set_event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n用哪个库？")
        self.run_events()
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_answer")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n自己定了，完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "review")
        self.assertTrue((self.repo.task_dir("T-001") / "evidence" / "done-D-001.md").exists())

    def test_done_blocked_by_gate_is_archived_not_lost(self) -> None:
        """T3 门未关时 DONE 不许推进（否则绕过人类裁决），但原文必须落盘、日志点名。"""
        self.assertEqual(foreman.main(["ask", "T-001", "--repo", str(self.root),
                                       "--question", "允许改 schema？"]), 0)
        self.assertEqual(self.repo.load_task("T-001")["status"], "waiting_human")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n我没等，先做完了")
        with self.assertRaises(SystemExit):
            self.run_events()
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "waiting_human", "门未关不得推进")
        ev = self.repo.task_dir("T-001") / "evidence" / "done-D-001.md"
        self.assertTrue(ev.exists(), "被拒收的 DONE 也必须留原文，不能只留一条报错")
        self.assertIn("我没等", ev.read_text(encoding="utf-8"))
        self.assertTrue(any("DONE 被拒收" in x["detail"] for x in task["log"]))

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

    def test_launch_falls_back_to_new_tree(self) -> None:
        """path: 旧树不可用（已被清）时降级 new-top-level，不阻断重派；旧树记进 stale_worktrees。"""
        cfg = foreman.effective_config(self.repo)
        tr = foreman.Orca(self.repo, cfg)
        calls = []

        def fake_run(*args, timeout=30):
            calls.append(args)
            if "--worktree" in args and args[args.index("--worktree") + 1].startswith("path:"):
                import subprocess as sp
                return sp.CompletedProcess(args, 1, "", "worktree not found")
            import subprocess as sp
            if "task-create" in args:
                return sp.CompletedProcess(args, 0, '{"ok":true,"result":{"id":"ot-1"}}', "")
            if "run-current" in args:
                return sp.CompletedProcess(args, 0, '{"ok":true,"result":{"runId":"run-1"}}', "")
            if "repo" in args and "list" in args:
                return sp.CompletedProcess(args, 0,
                                           '{"ok":true,"result":{"repos":[{"id":"repo-1",'
                                           '"path":"' + str(self.root) + '"}]}}', "")
            return sp.CompletedProcess(args, 0,
                                       '{"ok":true,"result":{"dispatchId":"d-9",'
                                       '"workspace":"repo-1::/tmp/new-wt",'
                                       '"branch":"foreman/T-001-impl"}}', "")

        with patch.object(tr, "_run", side_effect=fake_run):
            info = tr.launch("codex", "w-T-001-a2", "prompt", None, True, cfg,
                             reuse_worktree="/tmp/gone-wt")
        self.assertEqual(info["worktree"], "/tmp/new-wt")
        self.assertEqual(info["stale_worktrees"], ["/tmp/gone-wt"],
                         "降级新建必须把用不了的旧树记下来，供 merge/abandon 清理")
        ws = [c for c in calls if "worker-start" in c]
        self.assertEqual(len(ws), 2, "第一次 path: 失败后必须用 new-top-level 重试一次")
        self.assertEqual(ws[0][ws[0].index("--worktree") + 1], "path:/tmp/gone-wt")
        self.assertEqual(ws[1][ws[1].index("--worktree") + 1], "new-top-level")

    def test_merge_same_branch_two_trees(self) -> None:
        """Orca 重派复用树失败降级新建后：两个 dispatch 不同树、分支同名。

        merge 时 cleanup 删最后一次的树与分支，stale 清理删前一棵树，
        分支因同名不得再删（变异实验：去掉 keep_branch 判断会报"分支删除失败"）。
        """
        task = self.repo.load_task("T-001")
        task["status"] = "failed"
        self.repo.save_task(task)
        wt2 = Path(tempfile.mkdtemp(prefix="orca-wt2-")) / "wt"
        self.addCleanup(shutil.rmtree, wt2.parent, True)
        run_git(self.root, "worktree", "add", str(wt2), "-b", "foreman/T-001-impl-x", "master")
        os.environ["FAKE_ORCA_WT"] = str(wt2)
        self.assertEqual(foreman.main(["dispatch", "T-001", "--repo", str(self.root),
                                       "--retry-note", "缩 scope"]), 0)
        task = self.repo.load_task("T-001")
        d2 = task["dispatches"][-1]
        self.assertEqual(d2["worktree"], str(wt2), "降级新建得到新树")
        (wt2 / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(wt2, "feat: x")
        self.set_event(f"[FOREMAN:DONE] task=T-001 dispatch={d2['id']}\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        self.assertEqual(foreman.main(["merge", "T-001", "--repo", str(self.root)]), 0)
        task = self.repo.load_task("T-001")
        self.assertFalse(self.wt.exists(), "前一棵树（D-001）必须清掉")
        stale = " ".join(task["cleanup"].get("stale_attempts") or [])
        self.assertIn("树已删", stale)
        # 分支与 keep_branch 同名（Orca 各 attempt 同分支名）：stale 只删树，不碰分支
        self.assertNotIn("D-001 分支已删", stale, "同名分支已被 cleanup 删，stale 不得重复处理")
        self.assertNotIn("删除失败", stale)

    def test_retry_reuses_previous_worktree(self) -> None:
        """重派复用上一 attempt 的托管树：Orca 默认每次新建，会让树增殖（-2/-3 后缀）。"""
        task = self.repo.load_task("T-001")
        task["status"] = "failed"
        self.repo.save_task(task)
        before = len(self.orca_calls())
        self.assertEqual(foreman.main(["dispatch", "T-001", "--repo", str(self.root),
                                       "--retry-note", "缩 scope 到 src/"]), 0)
        calls = self.orca_calls()[before:]
        ws = next(c for c in calls if "worker-start" in c)
        self.assertEqual(ws[ws.index("--worktree") + 1], f"path:{self.wt}",
                         "重派必须指向旧树，否则 worktree 增殖且累计 diff 断掉")
        task = self.repo.load_task("T-001")
        self.assertEqual(task["dispatches"][-1]["attempt"], 2)

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

    def test_cleanup_failure_keeps_completed_and_records_it(self) -> None:
        """清理失败不能把已合并的任务退回 merge_ready：状态先落盘，失败只记进 cleanup。"""
        (self.wt / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(self.wt, "feat: x")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        # 真崩溃（不只是返回 False）：合并已落库，异常也不能让状态退回 merge_ready
        with patch.object(foreman.Orca, "cleanup", side_effect=RuntimeError("rm 崩了")):
            with self.assertRaises(RuntimeError):
                foreman.main(["merge", "T-001", "--repo", str(self.root)])
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "completed", "清理崩溃不许回退状态（否则会二次合并）")
        self.assertEqual(task["merge"]["commit"],
                         subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.root,
                                        capture_output=True, text=True).stdout.strip())
        # 文件系统：任务已归档，report 仍能出竣工单（不被清理失败挡住）
        self.assertEqual(foreman.main(["report", "T-001", "--repo", str(self.root)]), 0)

    def test_incomplete_cleanup_is_recorded_structurally(self) -> None:
        """cleanup 返回 False 时写结构化结果 + cleanup-incomplete 日志，供 doctor 核对。"""
        (self.wt / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(self.wt, "feat: x")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        boom = {"worktree_removed": True, "branch_deleted": True,
                "worker_released": False, "notes": ["worker release 失败（模拟）"]}
        with patch.object(foreman.Orca, "cleanup", return_value=boom):
            self.assertEqual(foreman.main(["merge", "T-001", "--repo", str(self.root)]), 0)
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "completed")
        self.assertFalse(task["cleanup"]["worker_released"])
        self.assertIn("worker release 失败", " ".join(task["cleanup"]["notes"]))
        self.assertIn("cleanup-incomplete", [e["event"] for e in task["log"]])
        self.assertTrue(foreman.resource_leaks(self.repo), "未完成的清理必须被 resource_leaks 看见")

    def test_orca_events_timeout(self) -> None:
        os.environ.pop("FAKE_ORCA_EVENT", None)
        self.assertEqual(self.run_events(), 1)


class TestResourceCleanup(TempGitRepoMixin):
    """合并/放弃后的资源回收：completed 是终态，没人回头看，清理必须幂等且不毁状态。"""

    def _orca_task(self) -> None:
        """Orca 模式下走到 completed 前一刻：任务已合并，dispatch 记录齐备。"""
        cfg = dict(self.repo.config(), transport="orca")
        foreman.write_json(self.repo.config_path, cfg)
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", {
            "id": "T-001", "title": "t", "slug": "t", "status": "completed", "attempts": 1,
            "scope": ["src/"], "acceptance": "true", "docs": {"progress": None, "decisions": []},
            "dispatches": [{"id": "D-001", "worker_handle": "dispatch:ctx-1", "run_id": "run-1",
                            "orchestration_task": "task-1", "worktree": str(self.root / "wt-x"),
                            "worktree_selector": "repo-1::/tmp/wt-x", "branch": "b"}],
            "asks": [], "gates": [], "decisions": [], "log": [], "created_at": "x", "updated_at": "x",
        })

    def test_cleanup_result_carries_dispatch_id(self) -> None:
        """清理结果要能指认是哪次派工——resource_leaks 的定位信息依赖它。"""
        o = foreman.Orca(self.repo, foreman.effective_config(self.repo))
        o.bin = str(FIXTURE_ORCA)
        res = o.cleanup(self.repo, {"id": "D-007", "worker_handle": "w", "worktree_selector": ""})
        self.assertEqual(res["dispatch"], "D-007")

    def test_hcom_cleanup_removes_tree_and_branch(self) -> None:
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        dispatch = task["dispatches"][-1]
        wt, branch = Path(dispatch["worktree"]), dispatch["branch"]
        self.assertTrue(wt.exists())
        res = foreman.Hcom(self.repo, self.repo.config()).cleanup(self.repo, dispatch)
        self.assertTrue(res["worktree_removed"])
        self.assertTrue(res["branch_deleted"])
        self.assertFalse(wt.exists())
        self.assertNotEqual(
            subprocess.run(["git", "rev-parse", "--verify", branch], cwd=self.root,
                           capture_output=True, text=True).returncode, 0)

    def test_hcom_cleanup_prunes_when_dir_already_gone(self) -> None:
        """目录被手工删掉（元数据残留）：prune 兜底，仍要删分支且不抛。"""
        self.full_task()
        self.dispatch()
        dispatch = self.repo.load_task("T-001")["dispatches"][-1]
        wt = Path(dispatch["worktree"])
        subprocess.run(["rm", "-rf", str(wt)], check=False)
        res = foreman.Hcom(self.repo, self.repo.config()).cleanup(self.repo, dispatch)
        self.assertTrue(res["worktree_removed"])
        self.assertTrue(res["branch_deleted"])
        self.assertIn("prune", " ".join(res["notes"]))

    def test_abandon_blocked_releases_resources(self) -> None:
        """blocked 不会再重派：worktree 留着就是永久泄漏，必须清。"""
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        task["attempts"] = 3
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", task)
        wt = Path(task["dispatches"][-1]["worktree"])
        self.assertEqual(foreman.main(["task", "abandon", "T-001", "--repo", str(self.root),
                                       "--reason", "worker 死亡"]), 0)
        after = self.repo.load_task("T-001")
        self.assertEqual(after["status"], "blocked")
        self.assertFalse(wt.exists(), "blocked 任务不应保留 worktree")
        self.assertIn("cleanup", after)

    def test_abandon_failed_keeps_worktree_for_retry(self) -> None:
        """failed 可重派且复用同一棵树：不能删。"""
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        wt = Path(task["dispatches"][-1]["worktree"])
        self.assertEqual(foreman.main(["task", "abandon", "T-001", "--repo", str(self.root),
                                       "--reason", "换个 agent 重来"]), 0)
        after = self.repo.load_task("T-001")
        self.assertEqual(after["status"], "failed")
        self.assertTrue(wt.exists(), "可重派任务的 worktree 必须保留复用")
        self.assertNotIn("cleanup", after)
        self.assertTrue(any(e["event"] == "cleanup-skipped" for e in after["log"]))

    def test_cleanup_failure_record_is_visible_even_without_dir(self) -> None:
        """worktree 目录已不在、但 cleanup 记录失败：靠结构化字段也要能看见。"""
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", {
            "id": "T-001", "title": "t", "status": "completed", "attempts": 1,
            "dispatches": [{"id": "D-001", "worktree": str(self.root / "gone")}],
            "cleanup": {"worktree_removed": False, "notes": ["rm 失败（模拟）"], "dispatch": "D-001"},
            "log": [], "docs": {}, "asks": [], "gates": [], "decisions": [],
        })
        leaks = foreman.resource_leaks(self.repo)
        self.assertEqual(len(leaks), 1)
        self.assertIn("清理未完成", leaks[0]["problem"])
        self.assertIn("rm 失败", leaks[0]["hint"])
        self.assertEqual(leaks[0]["dispatch"], "D-001", "泄漏记录必须带派工 id，否则定位不到是哪次派工")

    def test_doctor_reports_resource_leaks(self) -> None:
        task_dir = self.repo.task_dir("T-001")
        foreman.write_json(task_dir / "task.json", {
            "id": "T-001", "title": "t", "status": "completed", "attempts": 1,
            "dispatches": [{"id": "D-001", "worktree": str(self.root / "leftover")}],
            "log": [], "docs": {}, "asks": [], "gates": [], "decisions": [],
        })
        (self.root / "leftover").mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            foreman.main(["doctor", "--repo", str(self.root)])
        self.assertIn("[FAIL] resource-leaks", out.getvalue())
        leaks = foreman.resource_leaks(self.repo)
        self.assertEqual(len(leaks), 1)
        self.assertIn("leftover", leaks[0]["problem"])


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

    def test_quality_tools_status_reads_machine_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "tools" / "crapper").mkdir(parents=True)
            (home / "tools" / "crapper" / "crapper").write_text("#!/bin/sh\n")
            with patch.object(foreman.shutil, "which", return_value=None):
                st = {x["name"]: x["found"] for x in foreman.quality_tools_status(home=home)}
        self.assertTrue(st["crapper"], "~/tools/<name>/<name> 存在即视为已装")
        self.assertFalse(st["dryer"])
        self.assertFalse(st["mutator"])

    def test_doctor_quality_tools_missing_is_warn_not_fail(self) -> None:
        """可选工具缺失只提示：level=warn、ok 保持 True，不把体检判成失败。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "-q")
            foreman.write_json(root / ".vibe" / "config.json",
                               {"foreman_name": "foreman", "tag": "foreman", "default_agent": "codex",
                                "base": "main", "max_attempts": 3, "require_human_review": False,
                                "hcom_bin": "definitely-not-on-path-xyz"})
            missing = [{"name": n, "found": False, "path": ""} for n in foreman.QUALITY_TOOLS]
            with patch.object(foreman, "quality_tools_status", return_value=missing):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    foreman.main(["doctor", "--repo", str(root), "--json"])
            data = json.loads(out.getvalue())
        entry = [c for c in data["checks"] if c["name"] == "quality-tools"]
        self.assertEqual(len(entry), 1, "doctor 里必须有这条检查")
        self.assertEqual(entry[0]["level"], "warn")
        self.assertTrue(entry[0]["ok"], "warn 不是 fail")
        self.assertIn("crapper", entry[0]["detail"])
        self.assertIn("unclebob/crapper", entry[0]["fix"])

    def test_governance_budget_counts_total_chars_not_widest_line(self) -> None:
        """预算按「行数 + 总字符」判：单行很长但总量不超不算超；总量超了才 FAIL。

        单行上限会逼出别扭断行（2026-11-19 实测：为压一行字符重写整份 context.md），
        而「行数达标、每行塞满」这个真实风险由总字符数堵住，一次算总数也更可判定。
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "-q")
            foreman.write_json(root / ".vibe" / "config.json",
                               {"foreman_name": "foreman", "tag": "foreman", "default_agent": "codex",
                                "base": "main", "max_attempts": 3, "require_human_review": False,
                                "hcom_bin": "definitely-not-on-path-xyz"})
            (root / "AGENTS.md").write_text("x" * 500 + "\n" + "y\n" * 10, encoding="utf-8")
            with patch.object(foreman, "quality_tools_status", return_value=[]):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    foreman.main(["doctor", "--repo", str(root), "--json"])
            entry = [c for c in json.loads(out.getvalue())["checks"]
                     if c["name"] == "governance-budget"][0]
            self.assertTrue(entry["ok"], "单行 500 字符但总量在预算内，不该 FAIL")
            self.assertIn("字符", entry["detail"])

            (root / "AGENTS.md").write_text("z" * (foreman.AGENTS_BUDGET_TOTAL + 1), encoding="utf-8")
            with patch.object(foreman, "quality_tools_status", return_value=[]):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    foreman.main(["doctor", "--repo", str(root), "--json"])
            entry = [c for c in json.loads(out.getvalue())["checks"]
                     if c["name"] == "governance-budget"][0]
            self.assertFalse(entry["ok"], "总量超预算必须 FAIL")

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

    def test_orca_terminal_sender_is_not_flagged(self):
        """Orca 的 worker 消息 from 是终端句柄，不是 dispatch id：不能误报串线。"""
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import foreman
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = foreman.Repo(Path(tmp))
        cfg = dict(repo.load_or_default_config(), transport="orca")
        foreman.write_json(repo.config_path, cfg)
        task = {"id": "T-001", "title": "x", "status": "running", "attempts": 1, "log": [],
                "dispatches": [{"id": "D-001", "worker_handle": "dispatch:ctx_1"}]}
        foreman.write_json(repo.task_dir("T-001") / "task.json", task)
        res = foreman.apply_protocol_event(repo, cfg, {
            "id": "e1", "instance": "term_abc", "data": {
                "text": "[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成"}})
        self.assertNotIn("sender_unexpected", res)
        self.assertEqual(res["task_status"], "review")

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

    def test_escalation_is_surfaced_not_skipped(self) -> None:
        """escalation 是"worker 结束回合未上报"的权威信号：必须上行，不能当非协议消息跳过。"""
        self.assertEqual(foreman.main(["init", "--repo", str(self.root), "--transport", "orca"]), 0)
        ev = self.root / ".vibe" / "logs" / "fake-orca-event.json"
        ev.write_text(json.dumps({"id": "m1", "type": "escalation", "from": "term_x",
                                  "subject": "Worker stopped without reporting",
                                  "payload": json.dumps({"taskId": "task_1", "dispatchId": "ctx_1",
                                                         "stopReason": "finished"})}))
        os.environ["FAKE_ORCA_EVENT"] = str(ev)
        self.addCleanup(os.environ.pop, "FAKE_ORCA_EVENT", None)
        transport = foreman.Orca(self.repo, self.repo.config())
        out = transport.wait_message(1)
        self.assertNotIn("timed_out", out)
        self.assertEqual(out["escalation"]["orchestration_task"], "task_1")
        self.assertEqual(out["escalation"]["orchestration_dispatch"], "ctx_1")
        self.assertEqual(out["escalation"]["stop_reason"], "finished")


class TestReconcile(TempGitRepoMixin):
    """对账：在途任务 vs Orca 的 dispatch 状态与消息落地（纯消息驱动的盲区）。"""

    def _setup(self, worker: dict | None, messages: list[dict] | None = None,
               transport: str = "orca") -> foreman.Orca:
        cfg = foreman.effective_config(self.repo)
        cfg = dict(cfg, transport=transport)
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", {
            "id": "T-001", "title": "t", "slug": "t", "status": "running", "attempts": 1,
            "scope": ["src/"], "acceptance": "true", "docs": {"progress": None, "decisions": []},
            "dispatches": [{"id": "D-001", "worker_handle": "dispatch:ctx_1", "run_id": "run_1",
                            "orchestration_task": "task_1", "worktree": "/tmp/x"}],
            "asks": [], "gates": [], "decisions": [], "log": [], "created_at": "x", "updated_at": "x",
        })
        fake = self.root / "fake-orca-reconcile"
        payload = {"workers": [worker] if worker else []}
        inbox = {"messages": messages or []}
        fake.write_text("#!/bin/sh\ncase \"$2\" in\n"
                        f"  worker-list) echo '{json.dumps({'ok': True, 'result': payload})}' ;;\n"
                        f"  inbox) echo '{json.dumps({'ok': True, 'result': inbox})}' ;;\n"
                        "  *) echo '{\"ok\":true,\"result\":{}}' ;;\nesac\n")
        make_executable(fake)
        o = foreman.Orca(self.repo, cfg)
        o.bin = str(fake)
        return o

    def _findings(self, o: foreman.Orca, transport: str = "orca") -> list[dict]:
        cfg = dict(foreman.effective_config(self.repo), transport=transport)
        return foreman.reconcile_findings(self.repo, cfg, transport=o)

    def test_consistency_has_no_findings(self) -> None:
        o = self._setup({"dispatchId": "ctx_1", "state": "ready", "liveness": "live"})
        self.assertEqual(self._findings(o), [])

    def test_orca_succeeded_but_task_running(self) -> None:
        """Orca 说完成了，状态机没收到 DONE：这是消息被旁路消费的典型症状。"""
        o = self._setup({"dispatchId": "ctx_1", "state": "succeeded", "liveness": "exited"})
        findings = self._findings(o)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["level"], "fail")
        self.assertIn("DONE 没进状态机", findings[0]["problem"])

    def test_worker_exited_without_reporting(self) -> None:
        o = self._setup({"dispatchId": "ctx_1", "state": "ready", "liveness": "exited"})
        findings = self._findings(o)
        self.assertEqual([f["level"] for f in findings], ["fail"])
        self.assertIn("结束回合未上报", findings[0]["problem"])

    def test_orca_failed_but_task_running(self) -> None:
        o = self._setup({"dispatchId": "ctx_1", "state": "failed", "liveness": "exited"})
        findings = self._findings(o)
        self.assertIn("Orca 报 failed", findings[0]["problem"])

    def test_read_message_without_evidence(self) -> None:
        """消息被读走（read=1）却没有 evidence：说明是被裸 check 吃掉的。"""
        msg = {"id": "m1", "run_id": "run_1", "read": 1, "type": "status",
               "subject": "[FOREMAN:DONE] task=T-001 dispatch=D-001"}
        o = self._setup({"dispatchId": "ctx_1", "state": "ready", "liveness": "live"}, [msg])
        findings = self._findings(o)
        self.assertEqual(len(findings), 1)
        self.assertIn("没有落进状态机", findings[0]["problem"])
        self.assertIn("--peek", findings[0]["hint"])

    def test_landed_message_is_exempt_but_siblings_still_checked(self) -> None:
        """已落地的消息不算问题，但同批里没落地的照报——豁免不能退化成整批跳过。"""
        landed = {"id": "m1", "run_id": "run_1", "read": 1, "type": "status",
                  "subject": "[FOREMAN:ASK] task=T-001 dispatch=D-001"}
        lost = {"id": "m2", "run_id": "run_1", "read": 1, "type": "status",
                "subject": "[FOREMAN:ESCALATE] task=T-001 dispatch=D-002"}
        o = self._setup({"dispatchId": "ctx_1", "state": "ready", "liveness": "live"}, [landed, lost])
        evidence = self.repo.task_dir("T-001") / "evidence" / "ask-D-001.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("问过了", encoding="utf-8")
        findings = self._findings(o)
        self.assertEqual([f["dispatch"] for f in findings], ["D-002"])
        self.assertIn("ESCALATE", findings[0]["problem"])

    def test_other_run_messages_are_ignored(self) -> None:
        msg = {"id": "m1", "run_id": "run_other", "read": 1,
               "subject": "[FOREMAN:DONE] task=T-001 dispatch=D-001"}
        o = self._setup({"dispatchId": "ctx_1", "state": "ready", "liveness": "live"}, [msg])
        self.assertEqual(self._findings(o), [])

    def test_unread_message_is_not_a_finding(self) -> None:
        msg = {"id": "m1", "run_id": "run_1", "read": 0,
               "subject": "[FOREMAN:DONE] task=T-001 dispatch=D-001"}
        o = self._setup({"dispatchId": "ctx_1", "state": "ready", "liveness": "live"}, [msg])
        self.assertEqual(self._findings(o), [])

    def test_protocol_message_wins_over_escalation_in_same_batch(self) -> None:
        """同批 escalation + [FOREMAN:DONE]：协议消息优先，escalation 留档不丢。

        check 是消费式的：escalation 若抢先 return，同批 DONE 会静默消失（T-010 实测两条只隔 11s）。
        """
        o = self._setup({"dispatchId": "ctx_1", "state": "ready", "liveness": "live"})
        batch = [{"id": "e1", "type": "escalation", "from": "term_x",
                  "subject": "Worker stopped without reporting",
                  "payload": json.dumps({"taskId": "task_1", "dispatchId": "ctx_1"})},
                 {"id": "d1", "type": "status", "from": "term_x",
                  "subject": "[FOREMAN:DONE] task=T-010 dispatch=D-001", "body": "完工"}]
        fake = self.root / "fake-orca-batch"
        fake.write_text("#!/bin/sh\necho '" + json.dumps({"ok": True, "result": {"messages": batch}}) + "'\n")
        make_executable(fake)
        o.bin = str(fake)
        out = o.wait_message(1)
        self.assertNotIn("escalation", out)
        self.assertIn("[FOREMAN:DONE]", out["data"]["text"])
        self.assertIn("escalation", out.get("batch_extra", []))
        log = (self.root / ".vibe" / "logs" / "batch-unhandled.log").read_text(encoding="utf-8")
        self.assertIn("Worker stopped without reporting", log)

    def test_unknown_escalation_saves_evidence_before_failing(self) -> None:
        """认不出任务的 escalation：先留档再 fail，指针指向留档文件而不是 skipped 日志。"""
        ev = self.root / ".vibe" / "logs" / "fake-orca-event.json"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text(json.dumps({"id": "e9", "type": "escalation", "from": "term_x",
                                  "subject": "Worker stopped without reporting",
                                  "payload": json.dumps({"taskId": "task_noforun",
                                                         "dispatchId": "ctx_noforun",
                                                         "stopReason": "finished"})}))
        os.environ["FAKE_ORCA_EVENT"] = str(ev)
        self.addCleanup(os.environ.pop, "FAKE_ORCA_EVENT", None)
        foreman.write_json(self.repo.config_path, dict(self.repo.config(), transport="orca"))
        args = foreman.build_parser().parse_args(["events", "--repo", str(self.root)])
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            foreman.cmd_events(args)
        saved = list((self.repo.tasks_dir / "_unparsed" / "evidence").glob("escalation-*.md"))
        self.assertEqual(len(saved), 1, "escalation 原文必须在 fail 前留档")
        self.assertIn("留档", err.getvalue())
        self.assertIn("Worker stopped without reporting", err.getvalue())

    def test_pretty_printed_cli_json_is_parsed(self) -> None:
        """Orca 的 `inbox --full` 是多行 pretty JSON，只取最后一行会解析失败（实机踩到）。"""
        fake = self.root / "fake-orca-pretty"
        workers = {"ok": True, "result": {"workers": [{"dispatchId": "ctx_1", "state": "succeeded",
                                                      "liveness": "exited"}]}}
        fake.write_text("#!/bin/sh\ncase \"$2\" in\n"
                        f"  worker-list) printf '%s\\n' '{json.dumps(workers, indent=2)}' ;;\n"
                        "  *) echo '{\"ok\":true,\"result\":{}}' ;;\nesac\n")
        make_executable(fake)
        o = foreman.Orca(self.repo, foreman.effective_config(self.repo))
        o.bin = str(fake)
        self.assertEqual(len(o.workers(timeout=20, run="run_1")), 1)
        self.assertEqual(foreman.cli_json(o._run("orchestration", "worker-list", timeout=20))["ok"], True)

    def test_unverifiable_liveness_is_warn_not_fail(self) -> None:
        """liveness=unverifiable 只说明探测不了（远端终端），不是退出证据——warn 不 fail。"""
        o = self._setup({"dispatchId": "ctx_1", "state": "ready", "liveness": "unverifiable"})
        findings = self._findings(o)
        self.assertEqual([f["level"] for f in findings], ["warn"])
        self.assertIn("unverifiable", findings[0]["problem"])

    def test_unknown_orca_dispatch_is_warn(self) -> None:
        o = self._setup({"dispatchId": "ctx_other", "state": "ready", "liveness": "live"})
        findings = self._findings(o)
        self.assertEqual([f["level"] for f in findings], ["warn"])

    def test_hcom_dispatch_is_skipped(self) -> None:
        """hcom 派工记录是 @实例名，没有 Orca 权威状态可比——静默跳过，不报缺失。"""
        o = self._setup(None, transport="hcom")
        task = foreman.read_json(self.repo.task_dir("T-001") / "task.json")
        task["dispatches"] = [{"id": "D-001", "worker_handle": "@worker", "worktree": "/tmp/x"}]
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", task)
        self.assertEqual(self._findings(o, transport="hcom"), [])

    def test_json_output_marks_skipped(self) -> None:
        """hcom 派工时 JSON 的 skipped=true：与真通过同形会让机器消费者误读。"""
        o = self._setup(None, transport="hcom")
        task = foreman.read_json(self.repo.task_dir("T-001") / "task.json")
        task["dispatches"] = [{"id": "D-001", "worker_handle": "@worker", "worktree": "/tmp/x"}]
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", task)
        os.environ["ORCA_CLI_COMMAND"] = o.bin
        self.addCleanup(os.environ.pop, "ORCA_CLI_COMMAND", None)
        foreman.write_json(self.repo.config_path, dict(self.repo.config(), transport="hcom"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = foreman.main(["reconcile", "--repo", str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertTrue(data["skipped"])
        self.assertTrue(data["ok"])

    def test_orca_dispatch_without_run_id_warns(self) -> None:
        o = self._setup(None)
        task = foreman.read_json(self.repo.task_dir("T-001") / "task.json")
        task["dispatches"][0].pop("run_id")
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", task)
        findings = self._findings(o)
        self.assertEqual([f["level"] for f in findings], ["warn"])
        self.assertIn("run_id", findings[0]["problem"])

    def test_cli_exit_code_and_json(self) -> None:
        o = self._setup({"dispatchId": "ctx_1", "state": "succeeded", "liveness": "exited"})
        os.environ["ORCA_CLI_COMMAND"] = o.bin
        self.addCleanup(os.environ.pop, "ORCA_CLI_COMMAND", None)
        foreman.write_json(self.repo.config_path, dict(self.repo.config(), transport="orca"))
        self.assertEqual(foreman.main(["reconcile", "--repo", str(self.root)]), 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = foreman.main(["reconcile", "--repo", str(self.root), "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["findings"][0]["task"], "T-001")


class TestEscalation(TempGitRepoMixin):
    """escalation 落状态机：Orca 报"worker 结束回合未上报"时任务不能永远停在 running。"""

    def _task(self, status: str = "running") -> None:
        foreman.write_json(self.repo.task_dir("T-010") / "task.json", {
            "id": "T-010", "title": "t", "slug": "t", "status": status, "attempts": 1,
            "scope": ["src/"], "acceptance": "true", "docs": {"progress": None, "decisions": []},
            "dispatches": [{"id": "D-001", "worker_handle": "dispatch:ctx_1",
                            "orchestration_task": "task_1", "worktree": "/tmp/x"}],
            "asks": [], "gates": [], "decisions": [], "log": [], "created_at": "x", "updated_at": "x",
        })

    def _escalate(self) -> dict:
        return foreman.apply_protocol_event(self.repo, self.repo.config(), {
            "instance": "term_x",
            "data": {"text": "[FOREMAN:ESCALATE] task=T-010 dispatch=D-001\nWorker stopped without reporting"}})

    def test_running_task_goes_failed(self) -> None:
        self._task("running")
        res = self._escalate()
        self.assertEqual(res["task_status"], "failed")
        self.assertEqual(self.repo.load_task("T-010")["status"], "failed")
        self.assertTrue((self.repo.task_dir("T-010") / "evidence" / "escalate-D-001.md").exists())

    def test_waiting_answer_is_not_downgraded(self) -> None:
        """worker 发了 ASK 后结束回合等人：escalation 与那封 ASK 同源，别把等待变成失败。"""
        self._task("waiting_answer")
        res = self._escalate()
        self.assertEqual(res["task_status"], "waiting_answer")
        self.assertIn("reply", res["hint"])

    def test_late_escalation_does_not_regress_review(self) -> None:
        """任务已进 review（DONE 已消费）后再来 escalation：只记日志，不回退成 failed。"""
        self._task("review")
        res = self._escalate()
        self.assertEqual(res["task_status"], "review")
        self.assertIn("审查", res["hint"])
        self.assertEqual(self.repo.load_task("T-010")["status"], "review")

    def test_resolve_by_orchestration_ids(self) -> None:
        self._task("running")
        got = foreman.find_dispatch_by_orchestration(self.repo, "task_1", None)
        self.assertEqual(got, ("T-010", "D-001"))
        got = foreman.find_dispatch_by_orchestration(self.repo, None, "ctx_1")
        self.assertEqual(got, ("T-010", "D-001"))
        self.assertIsNone(foreman.find_dispatch_by_orchestration(self.repo, "task_other", "ctx_other"))

    def test_resolve_prefers_dispatch_id_over_task_id(self) -> None:
        """单 id 回退时 dispatchId 先于 taskId——taskId 先命中会在
        多次派工的任务上配错 dispatch（task 对、dispatch 错）。"""
        self._task("running")
        task = foreman.read_json(self.repo.task_dir("T-010") / "task.json")
        task["dispatches"] = [{"id": "D-001", "worker_handle": "dispatch:ctx_1",
                               "orchestration_task": "task_1"},
                              {"id": "D-002", "worker_handle": "dispatch:ctx_2"}]
        foreman.write_json(self.repo.task_dir("T-010") / "task.json", task)
        # ctx_2 的记录缺 orchestration_task：dispatchId 单独命中 D-002，而不是 taskId 命中的 D-001
        self.assertEqual(foreman.find_dispatch_by_orchestration(self.repo, "task_1", "ctx_2"),
                         ("T-010", "D-002"))
        # 两条记录都齐全时，dispatchId 仍然指认 D-002 而不是 taskId 先命中的 D-001
        task["dispatches"][1]["orchestration_task"] = "task_1"
        foreman.write_json(self.repo.task_dir("T-010") / "task.json", task)
        self.assertEqual(foreman.find_dispatch_by_orchestration(self.repo, "task_1", "ctx_2"),
                         ("T-010", "D-002"))

    def test_prompt_carries_dispatch_id(self) -> None:
        """worker_done 要 taskId + dispatchId 两样，prompt 漏一个就会白挨一次拒收（T-009 实测）。"""
        task = {"id": "T-010", "title": "t", "status": "ready", "attempts": 0, "scope": ["src/"],
                "acceptance": "true", "dispatches": [{"run_id": "run_x"}]}
        cfg = {"foreman_name": "f", "base": "main", "transport": "orca"}
        prompt = foreman.worker_prompt(cfg, task, {"id": "D-001"}, Path("/tmp/brief.md"))
        self.assertIn("--task-id <taskId>", prompt)
        self.assertIn("--dispatch-id <dispatchId>", prompt)


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

    def test_worker_prompt_pins_post_answer_behavior(self) -> None:
        """T-077/T-078 实测：worker 把「读到 ANSWER」当回合终点，收到裁决后挂起。

        死法：唤醒 → check 读到 ANSWER → 一条纯 thinking 消息（无工具调用）→ 回合结束。
        对 headless worker，结束回合 = 没人再叫醒。守卫文案在 worker_prompt
        （启动时注入的操作指令），两条通道都必须有。"""
        task = {"id": "T-1", "title": "x", "scope": [], "acceptance": "true"}
        dispatch = {"id": "D-1", "run_id": "run_abc"}
        brief = Path("/tmp/x/brief.md")
        for cfg in ({"foreman_name": "f", "base": "main", "transport": "orca"},
                    {"foreman_name": "f", "base": "main", "transport": "hcom"}):
            prompt = foreman.worker_prompt(cfg, task, dispatch, brief)
            self.assertIn("在同一回合继续实现", prompt)
            self.assertIn("读裁决不是回合终点", prompt)
            self.assertIn("唯一正确行为是继续实现", prompt)
        orca = foreman.worker_prompt({"foreman_name": "f", "base": "main",
                                      "transport": "orca"}, task, dispatch, brief)
        self.assertIn("结束回合 = 挂起", orca)
        # 口径有歧义先问：worker 把推论当事实用，猜错一轮就是整轮返工（2026-11-17 实测）
        for prompt in (orca, foreman.worker_prompt({"foreman_name": "f", "base": "main",
                                                    "transport": "hcom"}, task, dispatch, brief)):
            self.assertIn("先发 ASK 列选项与推荐项，不许猜着做", prompt)

    def test_brief_forbids_guessing_intent(self) -> None:
        """派工单与裁决口径有歧义必须问，不许猜（worker 只读 brief，不读 orchestration.md）。"""
        task = {"id": "T-1", "title": "x", "scope": [], "acceptance": "true", "dispatches": []}
        brief = foreman.brief_text({"foreman_name": "f", "base": "main",
                                    "transport": "hcom"}, task)
        self.assertIn("不许猜", brief)
        self.assertIn("把推论当事实用", brief)


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


class TestDecisionsIndex(TempGitRepoMixin):
    """决策索引双向核对：孤儿（未登记）与死链（指向不存在文件）都要报出来。"""

    def _index(self, *entries: str) -> None:
        d = self.root / "docs" / "decisions"
        d.mkdir(parents=True, exist_ok=True)
        (d / "README.md").write_text(
            "# 决策索引\n\n- YYYY-MM-DD｜<模块>｜[<决策标题>](YYYY-MM-DD-决策文件名.md)｜<状态>\n\n"
            + "\n".join(entries) + "\n", encoding="utf-8")

    def _decision(self, name: str) -> None:
        d = self.root / "docs" / "decisions"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("# 决策\n", encoding="utf-8")

    def test_consistent_index_reports_ok(self) -> None:
        self._decision("2026-01-01-a.md")
        self._decision("2026-01-02-b.md")
        self._index("- 2026-01-01｜x｜[a](2026-01-01-a.md)｜已生效",
                    "- 2026-01-02｜x｜[b](2026-01-02-b.md)｜已生效",
                    # 追记行重复登记同一文件属合法用法
                    "- 2026-01-02｜x｜[b 追记](2026-01-02-b.md)｜追记")
        rep = foreman.decisions_index_report(self.root / "docs" / "decisions")
        self.assertEqual(rep["orphans"], [])
        self.assertEqual(rep["dead"], [])
        self.assertEqual(rep["entries"], 3, "头部格式示例不应算条目")
        self.assertEqual(rep["files"], 2)

    def test_orphan_and_dead_link_are_both_reported(self) -> None:
        self._decision("2026-01-01-a.md")
        self._decision("2026-01-02-孤儿.md")
        self._index("- 2026-01-01｜x｜[a](2026-01-01-a.md)｜已生效",
                    "- 2026-01-03｜x｜[已删](2026-01-03-deleted.md)｜已生效")
        rep = foreman.decisions_index_report(self.root / "docs" / "decisions")
        self.assertEqual(rep["orphans"], ["2026-01-02-孤儿.md"])
        self.assertEqual(rep["dead"], ["2026-01-03-deleted.md"])

    def test_missing_index_is_reported(self) -> None:
        self._decision("2026-01-01-a.md")
        rep = foreman.decisions_index_report(self.root / "docs" / "decisions")
        self.assertTrue(rep["missing_index"])

    def test_anchor_link_counts_as_registered(self) -> None:
        """`file.md#追记` 是合法登记：只认 `file.md)` 会把已登记文件误报成孤儿。"""
        self._decision("2026-01-01-a.md")
        self._index("- 2026-01-01｜x｜[a](2026-01-01-a.md#追记)｜已生效")
        rep = foreman.decisions_index_report(self.root / "docs" / "decisions")
        self.assertEqual(rep["orphans"], [])
        self.assertEqual(rep["dead"], [])

    def test_wrapped_entry_counts_as_registered(self) -> None:
        """长条目折行时链接落在缩进续行：只解析条目首行会误报孤儿。"""
        self._decision("2026-01-01-a.md")
        d = self.root / "docs" / "decisions"
        d.mkdir(parents=True, exist_ok=True)
        (d / "README.md").write_text(
            "# 决策索引\n\n- 2026-01-01｜x｜[a](\n  2026-01-01-a.md)｜已生效\n", encoding="utf-8")
        rep = foreman.decisions_index_report(d)
        self.assertEqual(rep["orphans"], [])
        self.assertEqual(rep["dead"], [])

    def test_empty_index_without_decisions_is_ok(self) -> None:
        """新项目：只有索引骨架、一份决策都没有，属正常态（不是格式错误）。"""
        d = self.root / "docs" / "decisions"
        d.mkdir(parents=True, exist_ok=True)
        (d / "README.md").write_text("# 决策索引\n\n- YYYY-MM-DD｜<模块>｜[<标题>](YYYY-MM-DD-x.md)\n",
                                     encoding="utf-8")
        rep = foreman.decisions_index_report(d)
        self.assertFalse(rep["unreadable"])
        self.assertEqual((rep["files"], rep["entries"], rep["orphans"], rep["dead"]), (0, 0, [], []))

    def test_unreadable_index_is_reported_separately(self) -> None:
        """一条条目都解析不出时要说"格式不认识"，不能把全部文件报成未登记。"""
        self._decision("2026-01-01-a.md")
        d = self.root / "docs" / "decisions"
        d.mkdir(parents=True, exist_ok=True)
        (d / "README.md").write_text("# 索引\n\n* 2026-01-01｜x｜[a](2026-01-01-a.md)\n", encoding="utf-8")
        rep = foreman.decisions_index_report(d)
        self.assertTrue(rep["unreadable"])
        self.assertEqual(rep["orphans"], [], "格式不认识时不该报孤儿")

    def test_link_outside_decisions_dir_is_not_dead(self) -> None:
        """条目链到 decisions 目录外的既有文件不算死链。"""
        self._decision("2026-01-01-a.md")
        (self.root / "docs" / "shared.md").write_text("# 共享\n", encoding="utf-8")
        self._index("- 2026-01-01｜x｜[a](2026-01-01-a.md)｜已生效",
                    "- 2026-01-02｜x｜[共享](../shared.md)｜已生效")
        rep = foreman.decisions_index_report(self.root / "docs" / "decisions")
        self.assertEqual(rep["dead"], [])
        self.assertEqual(rep["orphans"], [])

    def test_missing_decisions_dir_fails_doctor(self) -> None:
        """决策目录整个缺失时不能静默跳过检查（与 docs-progress 同口径）。"""
        (self.root / "docs" / "progress").mkdir(parents=True, exist_ok=True)
        (self.root / "AGENTS.md").write_text("# x\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = foreman.main(["doctor", "--repo", str(self.root), "--json"])
        out = buf.getvalue()
        data, _ = json.JSONDecoder().raw_decode(out[out.index("{"):])
        self.assertEqual(rc, 1)
        entry = [c for c in data["checks"] if c["name"] == "decisions-index"]
        self.assertEqual(len(entry), 1, "缺目录时这条检查仍须出现")
        self.assertFalse(entry[0]["ok"])
        self.assertNotIn("README.md", entry[0]["detail"],
                         "缺的是目录，别把提示说成缺索引文件（修法不同）")

    def test_doctor_fails_on_orphan_decision(self) -> None:
        self._decision("2026-01-01-a.md")
        self._decision("2026-01-02-孤儿.md")
        self._index("- 2026-01-01｜x｜[a](2026-01-01-a.md)｜已生效")
        (self.root / "docs" / "progress").mkdir(parents=True, exist_ok=True)
        (self.root / "AGENTS.md").write_text("# x\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = foreman.main(["doctor", "--repo", str(self.root), "--json"])
        out = buf.getvalue()
        data, _ = json.JSONDecoder().raw_decode(out[out.index("{"):])
        self.assertEqual(rc, 1)
        entry = [c for c in data["checks"] if c["name"] == "decisions-index"]
        self.assertEqual(len(entry), 1, "doctor 里必须有这条检查")
        self.assertFalse(entry[0]["ok"])
        self.assertIn("2026-01-02-孤儿.md", entry[0]["detail"])


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
        """同名目录靠路径哈希区分：两个 api/ 都撞上 api-foreman 时应挑到不同名字。"""
        self._set_list([{"name": "api-foreman", "directory": "/tmp/other-repo"}])
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = Path(a) / "api", Path(b) / "api"
            ra.mkdir(), rb.mkdir()
            na, ta = foreman.pick_foreman_names(foreman.Repo(ra), str(FIXTURE_HCOM))
            nb, tb = foreman.pick_foreman_names(foreman.Repo(rb), str(FIXTURE_HCOM))
            self.assertNotEqual(na, nb)
            self.assertEqual(na, f"{ta}-foreman")

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


class TestAcceptanceShellHint(TempGitRepoMixin):
    """验收命令不经 shell：裸 && 会被当参数（实测 T-027 静默失效）。"""

    def test_shell_hint_flags_bare_operators(self) -> None:
        self.assertIn("bash -lc", foreman.shell_hint("pytest -q && ruff check ."))
        self.assertIn("bash -lc", foreman.shell_hint("pytest -q | tee out.log"))
        self.assertEqual(foreman.shell_hint("pytest -q"), "")
        self.assertEqual(foreman.shell_hint("bash -lc 'pytest -q && ruff check .'"), "",
                         "显式 bash -lc 是正确写法，不该再提示")
        self.assertEqual(foreman.shell_hint("python3 scripts/x.py --a=b"), "")

    def test_set_acceptance_warns_on_bare_and(self) -> None:
        self.full_task()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = foreman.main(["task", "set-acceptance", "T-001", "--repo", str(self.root),
                               "--accept", "pytest -q && ruff check ."])
        self.assertEqual(rc, 0, "提示不阻断（命令仍可用，只是会按字面执行）")
        self.assertIn("不经 shell", out.getvalue())


class TestAbandonCleansStaleAttempts(TempGitRepoMixin):
    """blocked 后 abandon：前几次 attempt 的树同样没用了（T-027 三次 attempt 留了两棵）。"""

    def test_abandon_blocked_removes_stale_trees(self) -> None:
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        stale_wt = Path(tempfile.mkdtemp(prefix="foreman-stale-")) / "wt"
        self.addCleanup(shutil.rmtree, stale_wt.parent, True)
        run_git(self.root, "worktree", "add", "-q", str(stale_wt), "-b", "foreman/T-001-old")
        task["dispatches"].insert(0, {
            "id": "D-000", "attempt": 1, "at": "2026-01-01T00:00:00Z",
            "worktree": str(stale_wt), "branch": "foreman/T-001-old",
            "worker_handle": "dispatch:ctx_old", "agent": "codex",
        })
        task["attempts"] = 3  # 阶梯用尽：abandon 会进 blocked 并清理
        self.repo.save_task(task)
        rc = foreman.main(["task", "abandon", "T-001", "--repo", str(self.root),
                           "--reason", "worker 不可恢复"])
        self.assertEqual(rc, 0)
        self.assertFalse(stale_wt.exists(), "blocked 任务的前几次树必须一起清")
        task = self.repo.load_task("T-001")
        self.assertEqual(task["status"], "blocked")
        self.assertIn("D-000 树已删", " ".join(task["cleanup"]["stale_attempts"]))


    def test_abandon_blocked_keeps_shared_branch(self) -> None:
        """hcom 模式所有 attempt 同一个分支名：清理只能删别的 dispatch 的分支，不能删在用的。"""
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        task["status"] = "failed"
        self.repo.save_task(task)
        self.dispatch(["--retry-note", "缩 scope"])
        task = self.repo.load_task("T-001")
        task["status"] = "running"
        task["attempts"] = 3
        self.repo.save_task(task)
        # abandon 在 attempts 用尽时置 blocked 并清理
        rc = foreman.main(["task", "abandon", "T-001", "--repo", str(self.root), "--reason", "交人"])
        self.assertEqual(rc, 0)
        task = self.repo.load_task("T-001")
        # blocked 语义 = 全清：cleanup 删最后一次 dispatch 的树与分支。
        # stale 清理只处理别的 dispatch；hcom 模式所有 attempt 同树同分支，
        # 必须按值跳过——否则会重复删或把已删的同名分支误判成 stale 再删一次。
        stale = " ".join(task["cleanup"].get("stale_attempts") or [])
        self.assertNotIn("分支删除失败", stale, "stale 不得重复操作 cleanup 已删的同名分支")
        self.assertNotIn("树删除失败", stale)
        self.assertNotIn("D-001 树已删", stale, "同树按值跳过，不该出现对 D-001 的删除记录")


    def test_abandon_blocked_stale_cleans_residue(self) -> None:
        """cleanup 删树失败时（树残留），stale 清理必须兜底把 D-001 的树删掉。

        变异实验：去掉「同树路径跳过」后 D-001 也能被处理，但当时树已被 cleanup 删掉，
        此测试专门覆盖树仍残留的情形。mock cleanup 让它不删 D-002 的树，
        两条 dispatch 同树，D-001 的残留靠 stale 清理兜底。
        """
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        task["status"] = "failed"
        self.repo.save_task(task)
        self.dispatch(["--retry-note", "缩 scope"])
        task = self.repo.load_task("T-001")
        task["status"] = "running"
        task["attempts"] = 3
        self.repo.save_task(task)
        # mock：cleanup 假装删失败（树残留）
        orig = foreman.make_transport

        def fake_transport(repo, cfg):
            tr = orig(repo, cfg)
            tr.cleanup = lambda repo_, d: {
                "dispatch": d.get("id"), "worktree_removed": False, "branch_deleted": False,
                "worker_released": None, "notes": ["rm 失败（模拟）"]}
            return tr

        with patch.object(foreman, "make_transport", fake_transport):
            rc = foreman.main(["task", "abandon", "T-001", "--repo", str(self.root),
                               "--reason", "交人"])
        self.assertEqual(rc, 0)
        wt = Path(self.repo.load_task("T-001")["dispatches"][-1]["worktree"])
        self.assertFalse(wt.exists(), "cleanup 失败的树必须由 stale 清理兜底删掉")
        task = self.repo.load_task("T-001")
        self.assertIn("D-001 树已删", " ".join(task["cleanup"]["stale_attempts"]),
                      "同树但 cleanup 没删成时，stale 要删")


class TestMergeCleansStaleAttempts(TempGitRepoMixin):
    """任务完成时，被淘汰 attempt 的树与分支也要清：Orca 每个 attempt 一棵新树。

    实测 T-036 的 D-001/D-002 两棵留在 ~/orca/workspaces，doctor 的 resource-leaks 报出来。
    """

    def test_merge_removes_stale_attempt_trees(self) -> None:
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        stale_wt = Path(tempfile.mkdtemp(prefix="foreman-stale-")) / "wt"
        self.addCleanup(shutil.rmtree, stale_wt.parent, True)
        run_git(self.root, "worktree", "add", "-q", str(stale_wt), "-b", "foreman/T-001-old")
        task["dispatches"].insert(0, {
            "id": "D-000", "attempt": 1, "at": "2026-01-01T00:00:00Z",
            "worktree": str(stale_wt), "branch": "foreman/T-001-old",
            "worker_handle": "dispatch:ctx_old", "agent": "codex",
        })
        self.repo.save_task(task)

        wt = Path(task["dispatches"][-1]["worktree"])
        (wt / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(wt, "feat: x")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        self.assertEqual(foreman.main(["merge", "T-001", "--repo", str(self.root)]), 0)

        self.assertFalse(stale_wt.exists(), "被淘汰 attempt 的树必须删，否则永久占磁盘")
        branches = subprocess.run(["git", "branch", "--list", "foreman/T-001-old"],
                                  cwd=self.root, capture_output=True, text=True).stdout
        self.assertEqual(branches.strip(), "", "被淘汰 attempt 的分支也要删")
        task = self.repo.load_task("T-001")
        self.assertIn("D-000 树已删", " ".join(task["cleanup"]["stale_attempts"]))


    def test_merge_hcom_same_branch_not_double_deleted(self) -> None:
        """hcom 模式所有 attempt 同树同分支：merge 后 stale 清理不得删在用的分支。

        变异实验发现：去掉 keep_branch 判断后此场景会把已合并的分支删掉（merge 已完成，
        产物不坏，但分支凭空消失）。正确行为是按值跳过。
        """
        self.full_task()
        self.dispatch()
        task = self.repo.load_task("T-001")
        task["status"] = "failed"
        self.repo.save_task(task)
        self.dispatch(["--retry-note", "缩 scope"])
        wt = Path(self.repo.load_task("T-001")["dispatches"][-1]["worktree"])
        (wt / "src" / "feature.txt").write_text("done\n", encoding="utf-8")
        self.commit_in(wt, "feat: x")
        self.set_event("[FOREMAN:DONE] task=T-001 dispatch=D-002\n完成")
        self.assertEqual(self.run_events(), 0)
        self.assertEqual(foreman.main(["review", "T-001", "--repo", str(self.root)]), 0)
        self.assertEqual(foreman.main(["merge", "T-001", "--repo", str(self.root)]), 0)
        branches = subprocess.run(["git", "branch", "--list", "foreman/T-001-实现-x"],
                                  cwd=self.root, capture_output=True, text=True).stdout
        # merge 自己会删合并的分支；关键是 stale 清理不得报错或重复删
        task = self.repo.load_task("T-001")
        stale = " ".join(task["cleanup"].get("stale_attempts") or [])
        self.assertNotIn("删除失败", stale)


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
