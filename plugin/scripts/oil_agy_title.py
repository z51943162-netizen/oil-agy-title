#!/usr/bin/env python3
"""Antigravity 桌面端话题自动命名入口；Stop Hook 快速派生后台 worker 并立即返回 {}。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from agy_adapter import (
    AntigravityBackend,
    BackendError,
    ModelSkipped,
    app_data_dir,
    find_agy,
    generate_title,
)
import file_lock
from usage_ledger import usage_report

PLUGIN_ROOT = SCRIPT_DIR.parent
DEFAULTS = {
    "enabled": True,
    "model": "gemini-3.8-flash-low",
    "agy_bin": None,
    "recent_turns": 5,
    "max_context_chars": 14000,
    "model_timeout_seconds": 60,
    "max_parallel_workers": 2,
    "allow_pbtxt_fallback": True,
    "windows_auto_preview": False,
}
EMOJI = ("🎬", "🧩", "🔎", "📝", "📅", "🎨", "⚙️", "💬")
POLICY_VERSION = 1


def data_dir() -> Path:
    override = os.environ.get("OIL_AGY_TITLE_DATA")
    if override:
        return Path(override).expanduser()
    return app_data_dir() / "plugin_data" / "oil-agy-title"


def read_json(path: Path, default=None) -> dict:
    if not path.exists():
        return {} if default is None else default
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON 文件必须是对象")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def load_config(root: Path) -> dict:
    config = DEFAULTS | read_json(root / "config.json")
    if not isinstance(config["windows_auto_preview"], bool):
        raise ValueError("windows_auto_preview 必须是布尔值")
    if sys.platform == "win32":
        config["allow_pbtxt_fallback"] = False
    if not isinstance(config["enabled"], bool):
        raise ValueError("enabled 必须是布尔值")
    if not isinstance(config["allow_pbtxt_fallback"], bool):
        raise ValueError("allow_pbtxt_fallback 必须是布尔值")
    for key, lower, upper in (
        ("recent_turns", 3, 8),
        ("max_context_chars", 3000, 20000),
        ("model_timeout_seconds", 10, 180),
        ("max_parallel_workers", 1, 8),
    ):
        if type(config[key]) is not int or not lower <= config[key] <= upper:
            raise ValueError(f"{key} 必须在 {lower}～{upper} 之间")
    if not isinstance(config["model"], str) or not config["model"].strip():
        raise ValueError("model 不能为空")
    return config


def valid_id(value: str) -> str:
    return str(uuid.UUID(value))


@contextmanager
def conversation_lock(root: Path, conversation_id: str, wait_seconds: float = 0):
    path = root / "locks" / f"{conversation_id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                file_lock.acquire(stream)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        try:
            yield True
        finally:
            file_lock.release(stream)


@contextmanager
def worker_slot(root: Path, limit: int, wait_seconds: float):
    deadline = time.monotonic() + wait_seconds
    while True:
        for index in range(limit):
            with conversation_lock(root / "worker-pool", str(index)) as acquired:
                if acquired:
                    yield True
                    return
        if time.monotonic() >= deadline:
            yield False
            return
        time.sleep(min(0.15, max(0.0, deadline - time.monotonic())))


def state_path(root: Path, conversation_id: str) -> Path:
    return root / "threads" / f"{conversation_id}.json"


def audit(root: Path, conversation_id: str, result: dict) -> None:
    path = root / "logs" / f"{conversation_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 256 * 1024:
        os.replace(path, path.with_suffix(".previous.jsonl"))
    entry = {"time": int(time.time()), **result}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def project_hint(conversation: dict) -> str:
    cwd = conversation.get("cwd") or ""
    if not cwd:
        return ""
    path = Path(cwd)
    if path == Path.home() or path.parent.name.lower() in ("users", "home"):
        return ""
    name = path.name
    if name.lower() in ("desktop", "documents", "downloads", "tmp", "project", "projects"):
        return ""
    return name[:64] if re.fullmatch(r"[\w .-]{1,64}", name) else ""


def snapshot(conversation: dict, config: dict) -> dict:
    turns = conversation.get("turns", [])
    effective = [t for t in turns if any(m.get("role") == "user" and m.get("text") for m in t.get("messages", []))]
    title = conversation.get("name") or ""
    selected = effective[-config["recent_turns"]:]
    budget = config["max_context_chars"]
    msg_count = max(1, sum(len(t["messages"]) for t in selected))
    per_message = max(200, (budget - 2000) // msg_count)
    recent = [
        {
            "id": t["id"],
            "messages": [
                {
                    "role": m["role"],
                    "text": m["text"][:min(per_message, 1200 if m["role"] == "user" else 500)],
                }
                for m in t["messages"]
            ],
        }
        for t in selected
    ]
    original = ""
    if effective:
        original = next(m["text"] for m in effective[0]["messages"] if m["role"] == "user")[:800]
    latest_id = effective[-1]["id"] if effective else None
    context = {
        "current_title": title,
        "project_hint": project_hint(conversation),
        "original_goal": original,
        "recent_turns": recent,
    }
    signature = json.dumps(
        {
            "policy_version": POLICY_VERSION,
            "project_hint": context["project_hint"],
            "latest_id": latest_id,
            "effective": [
                {"id": t["id"], "messages": t["messages"]}
                for t in selected
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    scope_basis = conversation.get("project_id") or conversation.get("cwd") or ""
    scope_key = hashlib.sha256(scope_basis.encode("utf-8")).hexdigest() if scope_basis else ""
    return {
        "title": title,
        "latest_id": latest_id,
        "fingerprint": fingerprint,
        "context": context,
        "has_messages": bool(effective),
        "turn_count": len(effective),
        "scope_key": scope_key,
    }


def validate_candidate(candidate: dict, current_title: str) -> dict:
    if not isinstance(candidate, dict) or set(candidate) != {"action", "title", "reason"}:
        raise ValueError("模型输出字段无效")
    if candidate["action"] not in ("rename", "keep") or not all(
        isinstance(candidate[k], str) for k in ("title", "reason")
    ):
        raise ValueError("模型输出类型无效")
    if candidate["action"] == "keep":
        candidate = {**candidate, "title": current_title}
    else:
        title = candidate["title"]
        if title != title.strip() or not 4 <= len(title) <= 48:
            raise ValueError("标题长度或空白无效")
        if not any(title.startswith(e + " ") for e in EMOJI):
            raise ValueError("标题缺少允许的类别 emoji")
        body = title.split(" ", 1)[1]
        if body.count("｜") != 1 or "|" in body:
            raise ValueError("标题必须采用对象｜目标结构")
        if any(not part or part != part.strip() for part in body.split("｜")):
            raise ValueError("标题对象与目标不能为空或带边缘空格")
        if (
            not body.strip()
            or any(e in body for e in EMOJI)
            or any(0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF for c in body)
        ):
            raise ValueError("标题正文无效或包含多个类别 emoji")
        if any(unicodedata.category(c).startswith("C") for c in title):
            raise ValueError("标题含控制字符")
        if re.search(r"[A-Za-z]:[\\/]|\\\\", title):
            raise ValueError("标题含 Windows 绝对路径")
        if any(x in title for x in ("\n", "\r", "`", "https://", "http://", "@", "/Users/", "sk-")):
            raise ValueError("标题含不允许的格式或私人信息")
    return {**candidate, "reason": candidate["reason"][:300]}


def confirmation_only(conversation: dict, state: dict, config: dict) -> int:
    """基线一致时跳过最多两轮纯确认；附件、遗漏轮次或规则升级均重新判断。"""
    if state.get("policy_version") != POLICY_VERSION or not state.get("last_fingerprint"):
        return 0
    current = conversation.get("name") or ""
    try:
        validate_candidate({"action": "rename", "title": current, "reason": ""}, current)
    except ValueError:
        return 0
    turns = [t for t in conversation.get("turns", []) if any(m.get("role") == "user" for m in t.get("messages", []))]
    index = next((i for i, t in enumerate(turns) if t["id"] == state.get("last_turn_id")), None)
    if index is None:
        return 0
    pending = turns[index + 1:]
    if not pending or len(pending) + state.get("confirmation_skips", 0) > 2:
        return 0
    if snapshot({**conversation, "turns": turns[:index + 1]}, config)["fingerprint"] != state["last_fingerprint"]:
        return 0
    allowed = {"好", "好的", "可以", "收到", "谢谢", "继续", "ok", "okay", "thanks", "thank you", "continue", "嗯", "行"}
    for turn in pending:
        if turn.get("has_attachments"):
            return 0
        raw = turn.get("raw_user_text") or ""
        if raw.strip(" \t\r\n.!。！").casefold() not in allowed:
            return 0
    return len(pending)


def conflicting_titles(backend: AntigravityBackend, root: Path, conversation_id: str,
                       candidate: str, scope_key: str, project_id: str) -> list[str]:
    conflicts: set[str] = set()
    for title in backend.list_titles(project_id, exclude_id=conversation_id):
        if title == candidate:
            conflicts.add(title)
    threads_dir = root / "threads"
    if threads_dir.is_dir():
        for path in threads_dir.glob("*.json"):
            if path.stem == conversation_id:
                continue
            try:
                st = read_json(path)
            except (ValueError, OSError):
                continue
            if scope_key and st.get("scope_key", "") != scope_key:
                continue
            if st.get("last_seen_title") == candidate:
                conflicts.add(candidate)
    return sorted(conflicts)


def ensure_title_active(backend: AntigravityBackend, conversation_id: str, root: Path) -> None:
    if not load_config(root)["enabled"]:
        raise ModelSkipped("disabled")
    if read_json(state_path(root, conversation_id)).get("locked"):
        raise ModelSkipped("locked")
    conv = backend.read(conversation_id)
    if conv.get("archived"):
        raise ModelSkipped("archived")


def limited_title(root: Path, conversation_id: str, config: dict, context: dict, *, before_model=None):
    deadline = time.monotonic() + config["model_timeout_seconds"]
    with worker_slot(root, config["max_parallel_workers"], config["model_timeout_seconds"]) as acquired:
        remaining = deadline - time.monotonic()
        if not acquired or remaining <= 0:
            raise BackendError("后台命名并发已满；本次保留原标题")
        return generate_title(
            root, conversation_id,
            {**config, "model_timeout_seconds": remaining},
            context, PLUGIN_ROOT, before_model=before_model,
        )


def process_conversation(
    backend: AntigravityBackend,
    generator,
    conversation_id: str,
    root: Path,
    config: dict,
    *,
    apply: bool = False,
    from_hook: bool = False,
    transcript_path: str | None = None,
    workspace_paths: list[str] | None = None,
    cwd: str | None = None,
) -> dict:
    conversation_id = valid_id(conversation_id)
    if not config["enabled"]:
        return {"status": "disabled"}
    if sys.platform == "win32" and apply:
        raise BackendError("Windows 预览版不支持自动写回")
    preview_hook = sys.platform == "win32" and from_hook
    if preview_hook and not config["windows_auto_preview"]:
        return {"status": "windows_hook_ready", "reason": "自动预览未启用"}

    wait_sec = config["model_timeout_seconds"] * 2 + 15 if from_hook else 0
    with conversation_lock(root, conversation_id, wait_sec) as acquired:
        if not acquired:
            return {"status": "busy"}

        # 后台刚触发时稍等落盘稳定；首轮若桌面端还在生成初始标题，短暂等待避免互相覆盖。
        if from_hook:
            time.sleep(1.2)

        path = state_path(root, conversation_id)
        state = read_json(path)
        conv = backend.read(conversation_id, transcript_path=transcript_path,
                            workspace_paths=workspace_paths, cwd=cwd)
        if conv.get("is_subagent"):
            return {"status": "subagent_skipped"}
        if conv.get("archived"):
            return {"status": "archived"}
        if from_hook and not conv.get("name") and len(conv.get("turns", [])) <= 1:
            time.sleep(1.5)
            conv = backend.read(conversation_id, transcript_path=transcript_path,
                                workspace_paths=workspace_paths, cwd=cwd)

        before = snapshot(conv, config)
        if not before["has_messages"]:
            return {"status": "empty"}

        if state.get("pending_title") == before["title"] and before["title"]:
            state.update(last_seen_title=before["title"], last_generated_title=before["title"])
            state.pop("pending_title", None)
            if apply:
                atomic_json(path, state)

        established = bool(state.get("last_fingerprint") or state.get("last_generated_title"))
        if state.get("locked") and state.get("lock_reason") == "检测到外部改名" and not established:
            state.update(locked=False, lock_reason="首次标题尚未建立稳定基线")
            if apply:
                atomic_json(path, state)
                audit(root, conversation_id, {"status": "initial_baseline_recovered"})
        if state.get("locked"):
            return {"status": "locked", "title": before["title"]}
        if preview_hook:
            if state.get("windows_preview_title") not in (None, before["title"]):
                state.update(locked=True, lock_reason="Windows 预览检测到外部改名")
                atomic_json(path, state)
                return {"status": "manual_title", "title": before["title"]}
            if state.get("windows_preview_turn") == before["latest_id"]:
                return {"status": "unchanged", "skip_reason": "same_preview_turn"}
            # Claim the turn before any model call, including failed attempts.
            state.update(windows_preview_turn=before["latest_id"],
                         windows_preview_title=before["title"])
            atomic_json(path, state)
        if established and "last_seen_title" in state and state["last_seen_title"] != before["title"]:
            if apply:
                state.update(locked=True, lock_reason="检测到外部改名", last_seen_title=before["title"])
                atomic_json(path, state)
            return {"status": "manual_title", "title": before["title"]}
        if state.get("last_fingerprint") == before["fingerprint"]:
            return {"status": "unchanged", "title": before["title"]}
        # 同一用户轮次因后台子任务多次触发 Stop 时，若已有合规标题则无需重复调模型。
        if (
            from_hook
            and state.get("policy_version") == POLICY_VERSION
            and state.get("last_turn_id") == before["latest_id"]
            and before["title"]
        ):
            try:
                validate_candidate({"action": "rename", "title": before["title"], "reason": ""}, before["title"])
                state["last_fingerprint"] = before["fingerprint"]
                atomic_json(path, state)
                return {"status": "unchanged", "title": before["title"], "skip_reason": "same_user_turn"}
            except ValueError:
                pass

        skipped = confirmation_only(conv, state, config)
        try:
            ensure_title_active(backend, conversation_id, root)
            if skipped:
                candidate = {"action": "keep", "title": before["title"], "reason": "新增内容仅为确认，保留稳定标题"}
                usage = {}
            else:
                candidate, usage = generator(before["context"])
        except ModelSkipped as exc:
            return {"status": exc.status}

        candidate = validate_candidate(candidate, before["title"])
        conflicts = conflicting_titles(backend, root, conversation_id, candidate["title"],
                                       before["scope_key"], conv.get("project_id", ""))
        if candidate["action"] == "rename" and conflicts:
            try:
                ensure_title_active(backend, conversation_id, root)
                candidate, retry_usage = generator({
                    **before["context"],
                    "conflicting_titles": conflicts,
                    "naming_feedback": "候选与已有会话重名。请用对话里真实的项目、模块或内容主题区分；无法区分就 keep，不编造编号。",
                })
            except ModelSkipped as exc:
                return {"status": exc.status, "usage": usage}
            candidate = validate_candidate(candidate, before["title"])
            usage = {k: int(usage.get(k, 0)) + int(retry_usage.get(k, 0)) for k in usage.keys() | retry_usage.keys()}
            if candidate["action"] == "rename" and conflicting_titles(
                backend, root, conversation_id, candidate["title"], before["scope_key"], conv.get("project_id", "")
            ):
                return {"status": "ambiguous_title", "title": before["title"], "usage": usage}

        result = {
            "status": "preview",
            **candidate,
            "previous_title": before["title"],
            "read_backend": conv.get("read_backend", ""),
            "usage": usage,
        }
        if skipped:
            result["skip_reason"] = "confirmation_only"
        if not apply:
            if preview_hook:
                current = load_config(root)
                if not current["enabled"] or not current["windows_auto_preview"]:
                    return {"status": "disabled"}
                after = snapshot(backend.read(conversation_id, transcript_path=transcript_path,
                                             workspace_paths=workspace_paths, cwd=cwd), config)
                if after["title"] != before["title"] or after["fingerprint"] != before["fingerprint"]:
                    return {"status": "stale_result", "title": after["title"]}
                if read_json(path).get("locked"):
                    return {"status": "locked"}
                result["status"] = "windows_preview"
            return result

        # 写入前再次检查：防止模型运行期间用户发了新消息、手动改名、暂停或归档。
        if not load_config(root)["enabled"]:
            return {"status": "disabled"}
        if read_json(path).get("locked"):
            return {"status": "locked"}
        after_conv = backend.read(conversation_id, transcript_path=transcript_path,
                                  workspace_paths=workspace_paths, cwd=cwd)
        if after_conv.get("archived"):
            return {"status": "archived"}
        after = snapshot(after_conv, config)
        if after["title"] != before["title"] or after["fingerprint"] != before["fingerprint"]:
            return {"status": "stale_result", "title": after["title"]}

        state.update(
            last_seen_title=before["title"],
            last_turn_id=before["latest_id"],
            scope_key=before["scope_key"],
            updated_at=int(time.time()),
            policy_version=POLICY_VERSION,
            confirmation_skips=state.get("confirmation_skips", 0) + skipped if skipped else 0,
        )
        if candidate["action"] == "rename" and candidate["title"] != before["title"]:
            state["pending_title"] = candidate["title"]
            atomic_json(path, state)
            write_backend = backend.rename(
                conversation_id,
                candidate["title"],
                allow_pbtxt_fallback=config["allow_pbtxt_fallback"],
            )
            verified = backend.read(conversation_id, transcript_path=transcript_path,
                                    workspace_paths=workspace_paths, cwd=cwd)
            if verified.get("name") != candidate["title"]:
                raise BackendError("标题写入后读回核验不一致")
            state.update(last_seen_title=candidate["title"], last_generated_title=candidate["title"])
            state.pop("pending_title", None)
            result["status"] = "renamed"
            result["write_backend"] = write_backend
        else:
            result["status"] = "kept"
        state["last_fingerprint"] = before["fingerprint"]
        atomic_json(path, state)
        audit(root, conversation_id, result)
        return result


def is_desktop_hook_event(event: dict) -> bool:
    paths = [str(event.get(key) or "").replace("\\", "/").casefold()
             for key in ("transcriptPath", "artifactDirectoryPath")]
    if any("antigravity-cli" in val or "antigravity-ide" in val for val in paths):
        return False
    if any("/.gemini/antigravity/" in val for val in paths):
        return True
    # 若载荷未带路径，检查 app_data_dir 下是否存在该对话目录。
    cid = event.get("conversationId") or ""
    if cid:
        try:
            return (app_data_dir() / "brain" / valid_id(cid)).is_dir()
        except (ValueError, TypeError, AttributeError):
            return False
    return False


def dispatch_hook(root: Path, config: dict) -> None:
    if os.environ.get("OIL_AGY_TITLE_WORKER") == "1" or not config["enabled"]:
        return
    raw = sys.stdin.read(1024 * 1024)
    if not raw.strip():
        return
    event = json.loads(raw)
    if event.get("hookEventName") and event.get("hookEventName") != "Stop":
        return
    if event.get("continuationActive"):
        return
    if event.get("fullyIdle") is False:
        return
    reason = str(event.get("terminationReason") or "")
    if reason in ("USER_CANCELED", "ERROR", "TERMINAL_CUSTOM_HOOK"):
        return
    if not is_desktop_hook_event(event):
        return
    cid = valid_id(event["conversationId"])
    if sys.platform == "win32" and not config["windows_auto_preview"]:
        audit(root, cid, {"status": "windows_hook_ready", "reason": "自动预览未启用"})
        return
    payload = {
        "conversationId": cid,
        "transcriptPath": event.get("transcriptPath") or "",
        "workspacePaths": event.get("workspacePaths") or [],
        "cwd": event.get("cwd") or "",
        "terminationReason": reason,
        "has_ls_env": bool(os.environ.get("ANTIGRAVITY_LS_ADDRESS") and os.environ.get("ANTIGRAVITY_CSRF_TOKEN")),
    }
    child_env = os.environ.copy()
    child_env["OIL_AGY_TITLE_WORKER"] = "1"
    spawn_options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32" else {"start_new_session": True}
    )
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "worker", "--payload-json", json.dumps(payload, ensure_ascii=False)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=child_env,
        **spawn_options,
    )


def doctor(backend: AntigravityBackend, root: Path, config: dict, conversation_id: str | None = None) -> dict:
    binary = find_agy(config.get("agy_bin"))
    run_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    ver = subprocess.run([binary, "--help"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=10, **run_options)
    output: dict = {
        "python": sys.version.split()[0],
        "plugin_root": str(PLUGIN_ROOT),
        "data_dir": str(root),
        "app_data_dir": str(backend.app_dir),
        "config": config,
        "rpc_env_present": backend.has_rpc,
        "platform": sys.platform,
        "title_write_supported": sys.platform != "win32",
        "hooks_file_exists": (PLUGIN_ROOT / "hooks.json").is_file(),
        "agy_bin": binary,
        "agy_ok": ver.returncode == 0,
    }
    if conversation_id:
        cid = valid_id(conversation_id)
        conv = backend.read(cid)
        snap = snapshot(conv, config)
        output["conversation"] = {
            "id": cid,
            "title": conv.get("name"),
            "archived": conv.get("archived"),
            "is_subagent": conv.get("is_subagent"),
            "read_backend": conv.get("read_backend"),
            "turn_count": snap["turn_count"],
            "has_messages": snap["has_messages"],
            "project_hint": snap["context"]["project_hint"],
        }
    return output


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Antigravity 桌面端独立模型话题自动命名")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hook", help="Stop Hook 入口：快速派生后台 worker 并返回 {}")
    p = sub.add_parser("worker", help="后台 worker（由 hook 内部派生）")
    p.add_argument("--payload-json", required=True)

    p = sub.add_parser("doctor", help="检查运行环境、接口与指定会话读取")
    p.add_argument("--conversation", help="会话 ID（默认读取 ANTIGRAVITY_CONVERSATION_ID）")

    sub.add_parser("status", help="显示配置、跟踪数量与最近日志")
    p = sub.add_parser("usage", help="汇总独立命名模型用量")
    p.add_argument("--days", type=int, default=30)
    sub.add_parser("pause", help="暂停自动命名")
    sub.add_parser("resume", help="恢复自动命名")

    p = sub.add_parser("configure", help="配置内置命名模型、agy 路径或回退开关")
    p.add_argument("--model")
    p.add_argument("--agy-bin")
    p.add_argument("--allow-pbtxt-fallback", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--windows-auto-preview", action=argparse.BooleanOptionalAction, default=None,
                   help="Windows 可选后台生成候选标题（会消耗模型额度，不写回会话）")

    for name in ("rename", "lock", "unlock"):
        p = sub.add_parser(name)
        p.add_argument("conversation_id", nargs="?", help="会话 ID（默认 ANTIGRAVITY_CONVERSATION_ID）")
        if name == "rename":
            p.add_argument("--apply", action="store_true", help="真正写入；省略时仅预览")

    args = parser.parse_args()
    root = data_dir()
    is_hook = args.command == "hook"
    is_worker = args.command == "worker"
    cid = None
    try:
        if sys.version_info < (3, 9):
            raise BackendError("需要 Python 3.9 或更新版本")
        config = load_config(root)

        if is_hook:
            dispatch_hook(root, config)
            return 0

        if is_worker:
            payload = json.loads(args.payload_json)
            cid = valid_id(payload["conversationId"])
            backend = AntigravityBackend()
            result = process_conversation(
                backend,
                lambda ctx: limited_title(
                    root, cid, config, ctx,
                    before_model=lambda: ensure_title_active(backend, cid, root),
                ),
                cid, root, config, apply=sys.platform != "win32", from_hook=True,
                transcript_path=payload.get("transcriptPath") or None,
                workspace_paths=payload.get("workspacePaths") or None,
                cwd=payload.get("cwd") or None,
            )
            if result.get("skip_reason") == "same_preview_turn":
                return 0  # Keep the previous suggestion visible in status.
            if result["status"] not in ("renamed", "kept"):
                audit(root, cid, {**result, "has_ls_env": payload.get("has_ls_env")})
            return 0

        if args.command in ("pause", "resume", "configure"):
            config_path = root / "config.json"
            changes = read_json(config_path)
            if args.command in ("pause", "resume"):
                changes["enabled"] = args.command == "resume"
            else:
                if args.model:
                    changes["model"] = args.model
                if args.agy_bin:
                    changes["agy_bin"] = find_agy(args.agy_bin)
                if args.allow_pbtxt_fallback is not None:
                    if sys.platform == "win32" and args.allow_pbtxt_fallback:
                        raise BackendError("Windows 预览版禁止 pbtxt 写回")
                    changes["allow_pbtxt_fallback"] = args.allow_pbtxt_fallback
                if args.windows_auto_preview is not None:
                    changes["windows_auto_preview"] = args.windows_auto_preview
            atomic_json(config_path, changes)
            print(json.dumps(load_config(root), ensure_ascii=False, indent=2))
            return 0

        if args.command == "status":
            threads_dir = root / "threads"
            tracked = len(list(threads_dir.glob("*.json"))) if threads_dir.is_dir() else 0
            logs_dir = root / "logs"
            recent_logs = []
            if logs_dir.is_dir():
                for lp in sorted(logs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                    lines = [ln for ln in lp.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                    if lines:
                        try:
                            recent_logs.append({"conversation_id": lp.stem, **json.loads(lines[-1])})
                        except ValueError:
                            pass
            print(json.dumps({
                "config": config,
                "data_dir": str(root),
                "tracked_conversations": tracked,
                "recent_events": recent_logs,
            }, ensure_ascii=False, indent=2))
            return 0

        if args.command == "usage":
            print(json.dumps(usage_report(root, days=args.days), ensure_ascii=False, indent=2))
            return 0

        backend = AntigravityBackend()
        if args.command == "doctor":
            target = args.conversation or os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or None
            print(json.dumps(doctor(backend, root, config, target), ensure_ascii=False, indent=2))
            return 0

        cid = valid_id(args.conversation_id or os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "")
        if args.command in ("lock", "unlock"):
            with conversation_lock(root, cid) as acquired:
                if not acquired:
                    raise BackendError("该会话正在命名，稍后再试")
                path = state_path(root, cid)
                state = read_json(path)
                current = backend.read(cid).get("name") or ""
                state.update(locked=args.command == "lock", lock_reason="用户设置", last_seen_title=current)
                state.pop("last_fingerprint", None)
                state.pop("pending_title", None)
                state.pop("windows_preview_turn", None)
                state.pop("windows_preview_title", None)
                atomic_json(path, state)
                print(json.dumps({"status": args.command, "conversation_id": cid, "title": current}, ensure_ascii=False))
                return 0

        result = process_conversation(
            backend,
            lambda ctx: limited_title(
                root, cid, config, ctx,
                before_model=lambda: ensure_title_active(backend, cid, root),
            ),
            cid, root, config, apply=args.apply, from_hook=False,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        err = {"status": "error", "error_type": type(exc).__name__, "message": str(exc)[:300]}
        if is_hook or is_worker:
            try:
                audit(root, cid or args.command, err)
            except Exception:
                pass
            return 0
        print(json.dumps(err, ensure_ascii=False, indent=2))
        return 1
    finally:
        if is_hook:
            print("{}")


if __name__ == "__main__":
    raise SystemExit(main())
