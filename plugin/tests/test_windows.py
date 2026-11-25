"""Offline regression tests for Windows preview; no live agy or session writes."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import agy_adapter
import oil_agy_title
from prepare_windows import prepare


class WindowsPreviewTests(unittest.TestCase):
    def test_prepare_native_hook_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            for launcher, prefix in (("py", "py -3"), ("python", "python")):
                output = Path(tmp) / launcher / "插件 space"
                prepare(output, launcher)
                hooks = json.loads((output / "hooks.json").read_text(encoding="utf-8"))
                self.assertEqual(hooks["oil-agy-title"]["Stop"][0]["command"],
                                 prefix + ' "./scripts/oil_agy_title.py" hook')
                with self.assertRaises(FileExistsError):
                    prepare(output, launcher)

    def test_windows_backend_never_writes_or_calls_rpc(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "platform", "win32"):
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_LS_ADDRESS": "example.invalid",
                                             "ANTIGRAVITY_CSRF_TOKEN": "dummy"}):
                backend = agy_adapter.AntigravityBackend(root)
                self.assertFalse(backend.has_rpc)
                with mock.patch.object(backend, "_rpc") as rpc:
                    with self.assertRaises(agy_adapter.BackendError):
                        backend.rename("fake", "title", allow_pbtxt_fallback=True)
                    rpc.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_config_disables_legacy_fallback_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "platform", "win32"):
            root = Path(tmp)
            (root / "config.json").write_text('{"allow_pbtxt_fallback":true}', encoding="utf-8")
            cfg = oil_agy_title.load_config(root)
            self.assertFalse(cfg["allow_pbtxt_fallback"])
            self.assertFalse(cfg["windows_auto_preview"])

    def test_default_hook_logs_only(self):
        event = {"conversationId": "c9c554bb-4afd-45aa-ae4d-1cae56e529c1",
                 "hookEventName": "Stop",
                 "transcriptPath": r"C:\Users\Name\.gemini\Antigravity\brain\transcript.jsonl"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "platform", "win32"):
            root = Path(tmp)
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event))), \
                    mock.patch.object(oil_agy_title.subprocess, "Popen") as popen, \
                    mock.patch.dict(os.environ, {"OIL_AGY_TITLE_WORKER": "0"}):
                oil_agy_title.dispatch_hook(root, oil_agy_title.load_config(root))
                popen.assert_not_called()
            entry = json.loads(next((root / "logs").glob("*.jsonl")).read_text(encoding="utf-8"))
            self.assertEqual(entry["status"], "windows_hook_ready")

    def test_stream_result(self):
        result = {"status": "SUCCESS", "response": '{"action":"keep","title":"","reason":""}'}
        raw = json.dumps({"event": "result", "result": result})
        self.assertEqual(agy_adapter._stream_result(raw + "\n"), result)
        for invalid in ("", "bad", raw + "\n" + raw, '{"event":"result","result":null}'):
            with self.assertRaises(agy_adapter.BackendError):
                agy_adapter._stream_result(invalid)

    def test_windows_preview_is_read_only_and_deduplicated(self):
        from test_title import FakeBackend
        cid = "11111111-2222-4333-8444-555555555555"
        conv = {"id": cid, "name": "Initial", "archived": False, "is_subagent": False,
                "project_id": "project", "cwd": "",
                "turns": [{"id": "0", "raw_user_text": "开发自动命名插件",
                           "messages": [{"role": "user", "text": "开发自动命名插件"}]}]}
        backend = FakeBackend(conv)
        generator = mock.Mock(return_value=(
            {"action": "rename", "title": "🧩 插件｜自动命名", "reason": "明确目标"}, {}))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(oil_agy_title.time, "sleep"):
            root = Path(tmp)
            (root / "config.json").write_text('{"windows_auto_preview":true}', encoding="utf-8")
            cfg = oil_agy_title.load_config(root)
            result = oil_agy_title.process_conversation(backend, generator, cid, root, cfg,
                                                        from_hook=True)
            self.assertEqual(result["status"], "windows_preview")
            self.assertEqual(result["title"], "🧩 插件｜自动命名")
            result = oil_agy_title.process_conversation(backend, generator, cid, root, cfg,
                                                        from_hook=True)
            self.assertEqual(result["status"], "unchanged")
            generator.assert_called_once()
            self.assertEqual(backend.renamed_to, [])
            with self.assertRaises(agy_adapter.BackendError):
                oil_agy_title.process_conversation(backend, generator, cid, root, cfg, apply=True)
            backend.conv["name"] = "用户手动设置"
            result = oil_agy_title.process_conversation(backend, generator, cid, root, cfg,
                                                        from_hook=True)
            self.assertEqual(result["status"], "manual_title")
            generator.assert_called_once()
            self.assertEqual(backend.renamed_to, [])

    def test_cli_path_exclusion(self):
        self.assertFalse(oil_agy_title.is_desktop_hook_event({
            "transcriptPath": r"C:\Users\User\.gemini\antigravity\brain\x",
            "artifactDirectoryPath": r"C:\Users\User\.gemini\ANTIGRAVITY-CLI\x",
        }))


if __name__ == "__main__":
    unittest.main()
