"""oil-agy-title 核心逻辑单元测试（不调用真实模型，不碰真实用户数据）。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agy_adapter
import oil_agy_title


class FakeBackend:
    def __init__(self, conv: dict, titles: set[str] | None = None):
        self.conv = json.loads(json.dumps(conv))
        self.titles = set(titles or set())
        self.renamed_to = []

    def read(self, conversation_id: str, **kwargs) -> dict:
        return json.loads(json.dumps(self.conv))

    def list_titles(self, project_id: str = "", *, exclude_id: str = "") -> set[str]:
        return set(self.titles)

    def rename(self, conversation_id: str, title: str, *, allow_pbtxt_fallback: bool = True) -> str:
        self.conv["name"] = title
        self.renamed_to.append(title)
        return "conversation_service"


class OilAgyTitleTests(unittest.TestCase):
    def test_pbtxt_parse_and_replace_preserves_other_fields(self):
        orig = 'title:"Old \\"Title\\"" last_user_view_time:{seconds:123 nanos:456} pinned:true'
        parsed = agy_adapter.parse_pbtxt_annotation(orig)
        self.assertEqual(parsed["title"], 'Old "Title"')
        self.assertTrue(parsed["pinned"])
        self.assertFalse(parsed["archived"])

        updated = agy_adapter.replace_pbtxt_title(orig, "🧩 oil-agy-title｜自动命名")
        self.assertIn('title:"🧩 oil-agy-title｜自动命名"', updated)
        self.assertIn("last_user_view_time:{seconds:123 nanos:456}", updated)
        self.assertIn("pinned:true", updated)

    def test_transcript_parser_extracts_user_request_and_final_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp) / "transcript.jsonl"
            lines = [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
                 "content": "<USER_REQUEST>\n帮我做个自动命名插件\n</USER_REQUEST>\n<ADDITIONAL_METADATA>x</ADDITIONAL_METADATA>"},
                {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
                 "thinking": "internal", "tool_calls": [{"name": "read_file"}]},
                {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE", "content": "tool output"},
                {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
                 "content": "好的，插件方案如下。"},
            ]
            tpath.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
            turns = agy_adapter.read_transcript_turns(tpath)
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["messages"][0], {"role": "user", "text": "帮我做个自动命名插件"})
            self.assertEqual(turns[0]["messages"][1], {"role": "assistant", "text": "好的，插件方案如下。"})

    def test_validate_candidate_rules(self):
        ok = oil_agy_title.validate_candidate(
            {"action": "rename", "title": "🧩 oil-agy-title｜自动命名", "reason": "明确目标"}, ""
        )
        self.assertEqual(ok["title"], "🧩 oil-agy-title｜自动命名")

        for bad in (
            "🧩 缺少分隔符",
            "🧩 用半角|分隔",
            "🔥 不允许的图标｜测试",
            "🧩 对象｜目标 🚀",
            "🧩 路径｜/Users/test",
            "🧩 边缘空格 ｜目标",
        ):
            with self.assertRaises(ValueError, msg=bad):
                oil_agy_title.validate_candidate({"action": "rename", "title": bad, "reason": "bad"}, "")

    @mock.patch.object(sys, "platform", "linux")
    def test_manual_title_protection_and_confirmation_skip(self):
        cid = "11111111-2222-4333-8444-555555555555"
        conv = {
            "id": cid,
            "name": "Initial Auto Title",
            "archived": False,
            "is_subagent": False,
            "project_id": "proj-1",
            "cwd": "/Users/example/Desktop",
            "read_backend": "conversation_service",
            "turns": [
                {"id": "0", "raw_user_text": "实现一个 Antigravity 自动命名插件", "has_attachments": False,
                 "messages": [{"role": "user", "text": "实现一个 Antigravity 自动命名插件"}]},
            ],
        }
        backend = FakeBackend(conv)
        calls = []

        def fake_gen(ctx):
            calls.append(ctx)
            return {"action": "rename", "title": "🧩 oil-agy-title｜自动命名插件", "reason": "明确开发目标"}, {"total_tokens": 100}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = oil_agy_title.load_config(root)

            # 第 1 轮：重命名并建立基线
            r1 = oil_agy_title.process_conversation(backend, fake_gen, cid, root, cfg, apply=True)
            self.assertEqual(r1["status"], "renamed")
            self.assertEqual(len(calls), 1)

            # 第 2 轮：纯确认「好的」应零模型调用直接跳过
            backend.conv["turns"].append(
                {"id": "4", "raw_user_text": "好的！", "has_attachments": False,
                 "messages": [{"role": "user", "text": "好的！"}]}
            )
            r2 = oil_agy_title.process_conversation(backend, fake_gen, cid, root, cfg, apply=True)
            self.assertEqual(r2["status"], "kept")
            self.assertEqual(r2.get("skip_reason"), "confirmation_only")
            self.assertEqual(len(calls), 1)

            # 用户手工改名后：应自动锁定并不调用模型
            backend.conv["name"] = "我手工起的标题"
            backend.conv["turns"].append(
                {"id": "8", "raw_user_text": "再加个用量统计", "has_attachments": False,
                 "messages": [{"role": "user", "text": "再加个用量统计"}]}
            )
            r3 = oil_agy_title.process_conversation(backend, fake_gen, cid, root, cfg, apply=True)
            self.assertEqual(r3["status"], "manual_title")
            self.assertEqual(len(calls), 1)
            r4 = oil_agy_title.process_conversation(backend, fake_gen, cid, root, cfg, apply=True)
            self.assertEqual(r4["status"], "locked")

    def test_desktop_hook_filter_blocks_cli(self):
        self.assertTrue(oil_agy_title.is_desktop_hook_event({
            "transcriptPath": "/Users/a/.gemini/antigravity/brain/123/.system_generated/logs/transcript.jsonl"
        }))
        self.assertFalse(oil_agy_title.is_desktop_hook_event({
            "transcriptPath": "/Users/a/.gemini/antigravity-cli/brain/123/.system_generated/logs/transcript.jsonl"
        }))

    @mock.patch.object(sys, "platform", "linux")
    def test_same_user_turn_hook_skips_repeat_model_call(self):
        cid = "22222222-3333-4444-8555-666666666666"
        conv = {
            "id": cid,
            "name": "Initial",
            "archived": False,
            "is_subagent": False,
            "project_id": "proj-1",
            "cwd": "/Users/example/Desktop",
            "read_backend": "pbtxt_fallback",
            "turns": [
                {"id": "0", "raw_user_text": "执行每日数据归因采集与同步任务", "has_attachments": False,
                 "messages": [
                     {"role": "user", "text": "执行每日数据归因采集与同步任务"},
                     {"role": "assistant", "text": "进度 1"},
                 ]},
            ],
        }
        backend = FakeBackend(conv)
        calls = []

        def fake_gen(ctx):
            calls.append(ctx)
            return {"action": "rename", "title": "📝 数据归因｜每日采集与同步", "reason": "首轮命名"}, {}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = oil_agy_title.load_config(root)
            r1 = oil_agy_title.process_conversation(backend, fake_gen, cid, root, cfg, apply=True)
            self.assertEqual(r1["status"], "renamed")
            self.assertEqual(len(calls), 1)
            # 同一用户轮次的助手回复更新时，from_hook 应直接跳过不重复调模型
            backend.conv["turns"][0]["messages"][1]["text"] = "进度 2：已完成发布"
            r2 = oil_agy_title.process_conversation(backend, fake_gen, cid, root, cfg, apply=True, from_hook=True)
            self.assertEqual(r2["status"], "unchanged")
            self.assertEqual(r2.get("skip_reason"), "same_user_turn")
            self.assertEqual(len(calls), 1)

    def test_normalize_project_prefix(self):
        c1 = agy_adapter.normalize_project_prefix(
            {"action": "rename", "title": "🧩 Kite LMS 邮箱验证｜修复过期", "reason": "ok"},
            {"project_hint": "kite-lms"},
        )
        self.assertEqual(c1["title"], "🧩 邮箱验证｜修复过期")
        c2 = agy_adapter.normalize_project_prefix(
            {"action": "rename", "title": "🧩 Kite 与 Maple｜校验同步", "reason": "ok"},
            {"project_hint": "kite"},
        )
        self.assertEqual(c2["title"], "🧩 Kite 与 Maple｜校验同步")

    def test_run_py_entrypoint_status_configure_and_preserve_unknown_keys(self):
        import os
        import subprocess
        run_py = Path(__file__).resolve().parents[1] / "skills" / "oil-agy-title" / "scripts" / "run.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({"custom_flag": "keep_me"}), encoding="utf-8")
            env = {**os.environ, "OIL_AGY_TITLE_DATA": str(root)}
            p1 = subprocess.run([sys.executable, str(run_py), "configure", "--model", "gemini-4-argon-low"],
                                capture_output=True, text=True, encoding="utf-8", env=env, check=True)
            cfg = json.loads(p1.stdout)
            self.assertEqual(cfg["model"], "gemini-4-argon-low")
            self.assertEqual(cfg["custom_flag"], "keep_me")
            p2 = subprocess.run([sys.executable, str(run_py), "pause"],
                                capture_output=True, text=True, encoding="utf-8", env=env, check=True)
            self.assertFalse(json.loads(p2.stdout)["enabled"])
            p3 = subprocess.run([sys.executable, str(run_py), "status"],
                                capture_output=True, text=True, encoding="utf-8", env=env, check=True)
            self.assertEqual(json.loads(p3.stdout)["config"]["model"], "gemini-4-argon-low")


if __name__ == "__main__":
    unittest.main()
