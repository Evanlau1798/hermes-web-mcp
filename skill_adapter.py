"""受限呼叫 Hermes 原生 ``skill_manage`` 工具。"""

import json
import importlib
import os
import re
from pathlib import Path

_SKILL = r"[a-z0-9][a-z0-9._-]{0,63}"
_NAME = {"type": "string", "description": "skills/ 根目錄內的技能名稱，可含一層分類。",
         "minLength": 1, "maxLength": 129,
         "pattern": rf"^(?:{_SKILL}/)?{_SKILL}$"}
_CREATE_NAME = {"type": "string", "description": "小寫技能名稱，不含分類路徑。",
                "minLength": 1, "maxLength": 64, "pattern": rf"^{_SKILL}$"}
_SUPPORT_PATH = {"type": "string", "description": "references/、templates/、scripts/ 或 assets/ 內的相對檔案路徑。",
                 "minLength": 1, "maxLength": 400,
                 "pattern": r"^(?:references|templates|scripts|assets)/.+$"}
_SKILL_OR_SUPPORT_PATH = {"type": "string", "minLength": 1, "maxLength": 400,
                          "description": "SKILL.md 或允許的支援目錄內相對路徑。",
                          "pattern": r"^(?:SKILL\.md|(?:references|templates|scripts|assets)/.+)$"}


def _operation(action, properties, required, name_schema=_NAME):
    descriptions = {
        "create": "在目前 Hermes skills 根目錄建立一份完整 SKILL.md。",
        "patch": "以精確片段替換修改，或明確提供 content 完整重寫 SKILL.md。",
        "write_file": "寫入技能的支援檔案。",
        "remove_file": "移除技能的支援檔案。",
        "delete": "刪除單一技能，仍受原生核准與安全檢查保護。",
    }
    return {"type": "object", "description": descriptions[action], "additionalProperties": False,
            "properties": {"action": {"const": action}, "name": name_schema, **properties},
            "required": ["action", "name", *required]}


_CONTENT = {"type": "string", "description": "完整技能或支援檔案文字，最多 100000 字元。",
            "maxLength": 100_000}
_TARGETED_PATCH = _operation("patch", {
    "old_string": _CONTENT, "new_string": _CONTENT,
    "replace_all": {"type": "boolean"}, "file_path": _SKILL_OR_SUPPORT_PATH,
}, ("old_string", "new_string"))
_FULL_PATCH = _operation("patch", {"content": _CONTENT}, ("content",))

SKILL_MANAGE_ADAPTER_SCHEMA = {
    "type": "object", "description": "受限管理 Hermes profile skills/ 內的技能；寫入交由原生核准流程處理。",
    "additionalProperties": False, "required": ["operations"],
    "properties": {"operations": {
        "description": "依序執行 1 到 20 個原生操作；delete 必須單獨執行。",
        "type": "array", "minItems": 1, "maxItems": 20,
        "items": {"oneOf": [
            _operation("create", {"content": _CONTENT,
                                   "category": {"type": "string", "pattern": rf"^{_SKILL}$"}},
                       ("content",), _CREATE_NAME),
            _TARGETED_PATCH,
            _FULL_PATCH,
            _operation("write_file", {"file_path": _SUPPORT_PATH,
                                       "file_content": _CONTENT}, ("file_path", "file_content")),
            _operation("remove_file", {"file_path": _SUPPORT_PATH}, ("file_path",)),
            _operation("delete", {"absorbed_into": _NAME}, ()),
        ]},
    }},
}
_SUPPORT_DIRS = {"references", "templates", "scripts", "assets"}
_SKILL_RE = re.compile(rf"^(?:{_SKILL}/)?{_SKILL}$")
_CREATE_NAME_RE = re.compile(rf"^{_SKILL}$")


def _redirected(path):
    try:
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
    except OSError:
        return True


def _inside_skills_root(path, root):
    try:
        root = Path(os.path.abspath(root))
        path = Path(os.path.abspath(path))
        relative = path.relative_to(root)
        if _redirected(root):
            return False
        resolved_root = root.resolve(strict=True)
        cursor = root
        for part in relative.parts:
            cursor /= part
            if _redirected(cursor):
                return False
        return path.resolve().is_relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return False


def _safe_file_path(value, action):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return None
    parts = value.split("/")
    if any(part in ("", ".", "..") or any(ord(char) < 32 for char in part) for part in parts):
        return None
    if action == "patch" and parts == ["SKILL.md"]:
        return Path("SKILL.md")
    if len(parts) >= 2 and parts[0] in _SUPPORT_DIRS:
        return Path(*parts)
    return None


def _shape_error(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {"operations"}:
        return "參數只接受 operations 陣列。"
    operations = arguments["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 20:
        return "operations 必須包含 1 到 20 個操作。"
    shapes = {
        "create": ({"name", "content", "category"}, {"name", "content"}),
        "write_file": ({"name", "file_path", "file_content"}, {"name", "file_path", "file_content"}),
        "remove_file": ({"name", "file_path"}, {"name", "file_path"}),
        "delete": ({"name", "absorbed_into"}, {"name"}),
    }
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or not isinstance(operation.get("action"), str) \
                or operation["action"] not in {"create", "patch", "write_file", "remove_file", "delete"}:
            return f"operations[{index}] 的 action 無效。"
        action = operation["action"]
        if action == "patch":
            targeted = "content" not in operation
            allowed = ({"action", "name", "old_string", "new_string", "replace_all", "file_path"}
                       if targeted else {"action", "name", "content"})
            required = {"action", "name", "old_string", "new_string"} if targeted else {
                "action", "name", "content"}
        else:
            properties, required_fields = shapes[action]
            allowed, required = properties | {"action"}, required_fields | {"action"}
        if set(operation) - allowed or not required <= set(operation):
            return f"operations[{index}] 的欄位與 {action} 操作不相符。"
        name = operation.get("name")
        name_re = _CREATE_NAME_RE if action == "create" else _SKILL_RE
        if not isinstance(name, str) or len(name) > (64 if action == "create" else 129) or not name_re.fullmatch(name):
            return f"operations[{index}].name 格式無效。"
        if "category" in operation and (action != "create" or not isinstance(operation["category"], str)
                                         or not _CREATE_NAME_RE.fullmatch(operation["category"])):
            return f"operations[{index}].category 格式無效。"
        if "replace_all" in operation and not isinstance(operation["replace_all"], bool):
            return f"operations[{index}].replace_all 必須是 boolean。"
        if "absorbed_into" in operation and (action != "delete" or not isinstance(operation["absorbed_into"], str)
                                              or not _SKILL_RE.fullmatch(operation["absorbed_into"])):
            return f"operations[{index}].absorbed_into 格式無效。"
        for key in ("content", "old_string", "new_string", "file_content"):
            if key in operation and (not isinstance(operation[key], str) or len(operation[key]) > 100_000):
                return f"operations[{index}].{key} 必須是至多 100000 字元的文字。"
        if "file_path" in operation and (not isinstance(operation["file_path"], str)
                                         or len(operation["file_path"]) > 400
                                         or _safe_file_path(operation["file_path"], action) is None):
            return f"operations[{index}].file_path 格式無效。"
    return None


def _target_error(manager, operations):
    root = Path(manager._skills_dir())
    created = {}
    for index, operation in enumerate(operations):
        action, name = operation["action"], operation["name"]
        if action == "create":
            target = manager._resolve_skill_dir(name, operation.get("category"))
            created[name] = target
        else:
            found = manager._find_skill(name)
            target = Path(found["path"]) if found else created.get(name, root / name)
        if not _inside_skills_root(target, root):
            return f"operations[{index}] 的目標不在 Hermes skills 根目錄內，或包含 symlink/junction。"
        if action in {"patch", "write_file", "remove_file"}:
            file_path = operation.get("file_path") or ("SKILL.md" if action == "patch" else None)
            if file_path is None:
                continue
            relative = _safe_file_path(file_path, action)
            if relative is None:
                return f"operations[{index}].file_path 必須是安全的 SKILL.md patch 路徑或支援目錄路徑。"
            if not _inside_skills_root(target / relative, root):
                return f"operations[{index}].file_path 會離開 Hermes skills 根目錄或穿越 symlink/junction。"
    return None


def skill_manage_adapter(arguments, *, native_dispatch, approval_callback=None,
                         task_id=None, session_id=None):
    """驗證參數與目標路徑，再經原生工具入口執行；不重播或略過核准。"""
    if error := _shape_error(arguments):
        return {"success": False, "errorCode": "invalid_arguments", "error": error}
    operations = arguments["operations"]
    if any(operation["action"] == "delete" for operation in operations) and len(operations) != 1:
        return {"success": False, "errorCode": "invalid_arguments",
                "error": "原生 skill_manage 僅允許 delete 單獨執行。"}

    try:
        from tools import skill_manager_tool
        error = _target_error(skill_manager_tool, operations)
    except Exception:
        return {"success": False, "errorCode": "path_rejected",
                "error": "無法安全確認 Hermes skills 根目錄與操作目標；未執行。"}
    if error:
        return {"success": False, "errorCode": "path_rejected", "error": error}

    try:
        gate = importlib.import_module('tools.write_approval')
        if not callable(getattr(gate, 'evaluate_gate', None)):
            raise ImportError('native approval unavailable')
    except ImportError:
        return {"success": False, "errorCode": "approval_unavailable",
                "error": "原生技能核准模組不可用；未執行，不略過防護。"}

    context = {key: value for key, value in (("task_id", task_id), ("session_id", session_id),
                                               ("approval_callback", approval_callback)) if value is not None}
    try:
        result = native_dispatch("skill_manage", arguments, **context)
    except Exception as exc:
        return {"success": False, "errorCode": "native_dispatch_failed", "error": str(exc)}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return {"success": False, "errorCode": "native_result_invalid",
                    "error": "原生 skill_manage 回傳非 JSON 結果；無法確認寫入狀態。"}
    if not isinstance(result, dict):
        return {"success": False, "errorCode": "native_result_invalid",
                "error": "原生 skill_manage 回傳格式無效；無法確認寫入狀態。"}
    if result.get("staged") is True:
        return {**result, "success": False, "errorCode": "approval_required",
                "approvalRequired": True,
                "error": result.get("message", "原生 skill_manage 已暫存；需先完成原生核准流程。")}
    return result
