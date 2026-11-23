"""噪声消息不许堵队列：落在已收口或未知任务上的协议消息只记一行，继续处理后面的。

通道是消费式的（一次 check 读走整批），一条 fail 就让同批后面的 ASK/DONE 全部搁浅，
而主管只看到 batch-unhandled.log 里一行摘要——工人等答复、主管等消息，双方都以为对方没发。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foreman  # noqa: E402


class TestNoiseDoesNotBlockQueue(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = foreman.Repo(Path(self.tmp))
        self.cfg = self.repo.load_or_default_config()
        foreman.write_json(self.repo.config_path, self.cfg)

    def _task(self, status: str) -> dict:
        task = {"id": "T-001", "title": "x", "status": status, "attempts": 1, "log": [],
                "dispatches": []}
        foreman.write_json(self.repo.task_dir("T-001") / "task.json", task)
        return task

    def _event(self, text: str) -> dict:
        return {"id": "e1", "instance": "term_x", "data": {"text": text, "from": "term_x"}}

    def test_late_done_on_completed_task_is_ignored_not_fatal(self) -> None:
        self._task("completed")
        res = foreman.apply_protocol_event(
            self.repo, self.cfg, self._event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n重复上报"))
        self.assertEqual(res["task_status"], "completed")
        self.assertIn("ignored", res)
        # 原文必须留档：消息已被通道消费，只记一行摘要等于丢了正文
        saved = Path(res["evidence"])
        self.assertTrue(saved.exists())
        self.assertIn("重复上报", saved.read_text(encoding="utf-8"))

    def test_late_ask_on_completed_task_is_ignored(self) -> None:
        self._task("completed")
        res = foreman.apply_protocol_event(
            self.repo, self.cfg, self._event("[FOREMAN:ASK] task=T-001 dispatch=D-002\n还在问"))
        self.assertEqual(res["task_status"], "completed")
        self.assertIn("ignored", res)

    def test_message_for_unknown_task_is_ignored(self) -> None:
        res = foreman.apply_protocol_event(
            self.repo, self.cfg, self._event("[FOREMAN:DONE] task=T-999 dispatch=D-001\n别仓的消息"))
        self.assertEqual(res["task_status"], "unknown")
        self.assertIn("ignored", res)
        self.assertIn("别仓的消息", Path(res["evidence"]).read_text(encoding="utf-8"))

    def test_ask_after_delivery_is_ignored(self) -> None:
        """交付后仍在提问的旧会话：忽略，不占队列。"""
        self._task("review")
        res = foreman.apply_protocol_event(
            self.repo, self.cfg, self._event("[FOREMAN:ASK] task=T-001 dispatch=D-001\n还能改吗"))
        self.assertEqual(res["task_status"], "review")
        self.assertIn("ignored", res)
        self.assertIn("还能改吗", Path(res["evidence"]).read_text(encoding="utf-8"))

    def test_ask_on_waiting_answer_still_records_question(self) -> None:
        """等待答复期间的再次提问是正常路径：要记进 asks，不能当噪声丢。"""
        self._task("waiting_answer")
        res = foreman.apply_protocol_event(
            self.repo, self.cfg, self._event("[FOREMAN:ASK] task=T-001 dispatch=D-002\n再问一个"))
        self.assertEqual(res["task_status"], "waiting_answer")
        self.assertNotIn("ignored", res)
        self.assertEqual(len(self.repo.load_task("T-001")["asks"]), 1)

    def test_normal_done_still_advances(self) -> None:
        self._task("running")
        res = foreman.apply_protocol_event(
            self.repo, self.cfg, self._event("[FOREMAN:DONE] task=T-001 dispatch=D-001\n完成"))
        self.assertEqual(res["task_status"], "review")
        self.assertNotIn("ignored", res)


if __name__ == "__main__":
    unittest.main()
