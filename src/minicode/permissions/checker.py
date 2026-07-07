"""参数级权限检查器。

基于工具名和参数进行细粒度权限判断，结果分为 safe / caution / dangerous / deny。
权限模型复用 src/minicode/tools/path_safety.py 中的路径检查规则，
不重复实现敏感文件检测或路径越界判断逻辑。

判断维度：
  - 路径是否在 workspace 内
  - 是否覆盖已有文件
  - 是否访问敏感文件
  - 是否删除或批量修改
  - shell 命令是否包含明显危险操作
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from minicode.permissions.models import PermissionDecision, PermissionLevel
from minicode.tools.path_safety import (
    SENSITIVE_FILE_PARTS,
    is_sensitive_file,
    is_within_workspace,
)

# 检查器函数签名
_CheckerFunc = Callable[[dict[str, object], Path], PermissionDecision]

# ---------------------------------------------------------------------------
# 拒绝级 shell 危险模式 — 永远不允许执行
# ---------------------------------------------------------------------------
_DENY_SHELL_PATTERNS: list[re.Pattern[str]] = [
    # rm -rf / (根目录递归删除)
    re.compile(r"(^|\s)rm\s+(-rf|[-]r[-]f|[-]f[-]r)\s+/\s*($|;|\|)"),
    # rm -rf /* (通配符根目录删除)
    re.compile(r"(^|\s)rm\s+(-rf|[-]r[-]f|[-]f[-]r)\s+/\*"),
    # sudo rm -rf /
    re.compile(r"(^|\s)sudo\s+rm\s+(-rf|[-]r[-]f|[-]f[-]r)\s+/\s*($|;|\|)"),
    # sudo rm -rf /*
    re.compile(r"(^|\s)sudo\s+rm\s+(-rf|[-]r[-]f|[-]f[-]r)\s+/\*"),
    # 带 --no-preserve-root 的 rm
    re.compile(r"rm\s+.*--no-preserve-root"),
    # Remove-Item -Recurse 作用于根
    re.compile(r"Remove-Item.*-Recurse.*[A-Za-z]:\\+$", re.IGNORECASE),
    re.compile(r"Remove-Item.*-Recurse.*[A-Za-z]:\\+\\\*", re.IGNORECASE),
    # Remove-Item -Recurse 作用于系统目录或 HOME
    re.compile(
        r"Remove-Item.*-Recurse.*[A-Za-z]:\\(?:Users|Windows|Program\s*Files)",
        re.IGNORECASE,
    ),
    re.compile(r"Remove-Item.*-Recurse.*\$HOME", re.IGNORECASE),
    # format / mkfs 等磁盘操作
    re.compile(r"(^|\s)format\s+\w+:?\s*", re.IGNORECASE),
    re.compile(r"(^|\s)mkfs\.\w+"),
    # dd 覆写设备
    re.compile(r"dd\s+if=.*of=\s*/dev/"),
    # fork 炸弹
    re.compile(r":\(\)\{\s*:\|\|:\s*\};?\s*:"),
    re.compile(r">\s*:\|\s*:\s*&"),
    # 清空所有文件
    re.compile(r"(^|\s)del\s+/f\s+/s\s+[A-Za-z]:\\", re.IGNORECASE),
    re.compile(r"(^|\s)rd\s+/s\s+/q\s+[A-Za-z]:\\", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# 危险级 shell 模式 — 需要用户确认
# ---------------------------------------------------------------------------
_DANGEROUS_SHELL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(^|\s)rm\s+-rf\s"),
    re.compile(r"(^|\s)rm\s+-r\s"),
    re.compile(r"git\s+reset\s+--hard\b"),
    re.compile(r"chmod\s+-R\b"),
    re.compile(r"(^|\s)del\s+/[sf]", re.IGNORECASE),
    re.compile(r"(^|\s)rd\s+/[sf]", re.IGNORECASE),
    re.compile(r"(^|\s)rmdir\s+/[sf]", re.IGNORECASE),
    re.compile(r"(^|\s)shutdown\b", re.IGNORECASE),
    re.compile(r"(^|\s)reboot\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# 宽泛 grep glob 模式 — 无有效文件类型过滤时增强确认
# ---------------------------------------------------------------------------
_BROAD_GLOB_PATTERNS: frozenset[str] = frozenset({"*", "**", "**/*", "**/*.*"})


def check_permission(
    tool_name: str,
    arguments: dict[str, object],
    workspace_root: Path,
    trust_mode: bool = False,
) -> PermissionDecision:
    """检查工具调用的权限级别。

    Args:
        tool_name: 工具名称
        arguments: 工具参数字典
        workspace_root: 工作区根目录（必须是绝对路径）
        trust_mode: 信任模式。为 True 时表示跳过确认交互直接执行，
                    但权限等级本身保持不变；deny 仍然拒绝执行。

    Returns:
        PermissionDecision: 权限判断结果
    """
    workspace_root = workspace_root.resolve()

    checker = _get_checker(tool_name)
    decision = checker(arguments, workspace_root)

    # trust_mode 不改变 level：权限模型必须保留原始风险等级。
    # trust_mode 只应作为后续确认层的输入。
    # 但 deny 仍然是 deny — 永远不可绕过。
    return decision


# ---------------------------------------------------------------------------
# 检查器注册表
# ---------------------------------------------------------------------------
_CHECKERS: dict[str, _CheckerFunc] = {}


def _register_checker(tool_name: str) -> Callable[[_CheckerFunc], _CheckerFunc]:
    """装饰器：注册工具检查器函数。"""
    def decorator(func: _CheckerFunc) -> _CheckerFunc:
        _CHECKERS[tool_name] = func
        return func
    return decorator


def _get_checker(tool_name: str) -> _CheckerFunc:
    """获取工具对应的检查器，未知工具返回默认拒绝检查器。"""
    checker = _CHECKERS.get(tool_name)
    if checker is not None:
        return checker
    return _make_unknown_checker(tool_name)


# ---------------------------------------------------------------------------
# 路径解析助手
# ---------------------------------------------------------------------------

def _resolve_arg_path(raw_path: str, workspace_root: Path) -> Path:
    """将参数字符串解析为规范化绝对路径。"""
    p = Path(raw_path)
    return p.resolve() if p.is_absolute() else (workspace_root / p).resolve()


def _is_broad_glob(glob_pattern: str | None) -> bool:
    """判断 grep 的 glob 参数是否为宽泛模式。"""
    if glob_pattern is None:
        return True
    return glob_pattern.strip() in _BROAD_GLOB_PATTERNS


def _is_sensitive_glob_pattern(pattern: str) -> bool:
    """检查 glob 模式是否指向敏感文件或路径。

    复用 path_safety.SENSITIVE_FILE_PARTS 的敏感规则，
    不重复定义敏感文件列表。

    覆盖模式：
      - 精确文件名匹配：.env、credentials.json
      - 路径片段匹配：.ssh/**、**/secrets/*
      - 扩展名匹配：*.pem、*.key
    """
    # 检查文件名部分是否直接命中敏感规则
    name = Path(pattern).name.lower()
    if name in SENSITIVE_FILE_PARTS:
        return True

    # 检查无扩展名的文件名（如 credentials.json → credentials）
    stem = Path(name).stem.lower()
    if stem in SENSITIVE_FILE_PARTS:
        return True

    # 检查扩展名（如 *.pem → .pem）
    suffix = Path(name).suffix.lower()
    if suffix in SENSITIVE_FILE_PARTS:
        return True

    # 检查路径每一部分（如 .ssh/** → .ssh 在 parts 中）
    for part in Path(pattern).parts:
        part_lower = part.lower()
        if part_lower in SENSITIVE_FILE_PARTS:
            return True

    return False


# ---------------------------------------------------------------------------
# read_file 检查器
# ---------------------------------------------------------------------------

@_register_checker("read_file")
def _check_read_file(
    arguments: dict[str, object],
    workspace_root: Path,
) -> PermissionDecision:
    file_path = arguments.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="read_file",
            operation="读取文件",
            summary="read_file：缺少有效的 file_path 参数",
            reasons=["file_path 参数缺失或无效"],
        )

    target = _resolve_arg_path(file_path, workspace_root)

    # workspace 越界
    if not is_within_workspace(target, workspace_root):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="read_file",
            operation="读取文件",
            summary=f"read_file：拒绝访问工作区外路径 {target}",
            target_paths=[target],
            reasons=[f"路径 {target} 不在工作区 {workspace_root} 范围内"],
        )

    # 敏感文件
    if is_sensitive_file(target):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="read_file",
            operation="读取文件",
            summary=f"read_file：拒绝读取敏感文件 {target}",
            target_paths=[target],
            reasons=[f"{target} 属于敏感文件，不允许读取"],
        )

    # 正常文件
    return PermissionDecision(
        level=PermissionLevel.SAFE,
        tool_name="read_file",
        operation="读取文件",
        summary=f"read_file：读取文件 {target}",
        target_paths=[target],
        reasons=["路径在工作区内且非敏感文件"],
    )


# ---------------------------------------------------------------------------
# grep 检查器
# ---------------------------------------------------------------------------

@_register_checker("grep")
def _check_grep(
    arguments: dict[str, object],
    workspace_root: Path,
) -> PermissionDecision:
    pattern = arguments.get("pattern", "")
    glob_param = arguments.get("glob")

    if not isinstance(pattern, str) or not pattern.strip():
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="grep",
            operation="搜索文件内容",
            summary="grep：缺少有效的 pattern 参数",
            reasons=["pattern 参数缺失或无效"],
        )

    glob_value: str | None = None
    if isinstance(glob_param, str) and glob_param.strip():
        glob_value = glob_param.strip()

    if _is_broad_glob(glob_value):
        return PermissionDecision(
            level=PermissionLevel.CAUTION,
            tool_name="grep",
            operation="搜索文件内容",
            summary=f"grep：在全部文件中搜索 '{pattern[:80]}'（无文件类型过滤）",
            target_paths=[workspace_root],
            reasons=["grep 未指定文件类型过滤，可能遍历大量文件"],
        )

    # 检查 glob 是否指向敏感文件（如 .env、*.pem、.ssh/**）
    if glob_value and _is_sensitive_glob_pattern(glob_value):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="grep",
            operation="搜索文件内容",
            summary=f"grep：拒绝在敏感路径模式 '{glob_value}' 中搜索",
            target_paths=[workspace_root],
            reasons=[f"glob 模式 '{glob_value}' 指向敏感文件，不允许搜索"],
        )

    return PermissionDecision(
        level=PermissionLevel.SAFE,
        tool_name="grep",
        operation="搜索文件内容",
        summary=f"grep：在 {glob_value} 文件中搜索 '{pattern[:80]}'",
        reasons=[f"搜索范围限定在 {glob_value} 文件内"],
    )


# ---------------------------------------------------------------------------
# glob 检查器
# ---------------------------------------------------------------------------

@_register_checker("glob")
def _check_glob(
    arguments: dict[str, object],
    workspace_root: Path,
) -> PermissionDecision:
    pattern = arguments.get("pattern")

    if not isinstance(pattern, str) or not pattern.strip():
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="glob",
            operation="匹配文件路径",
            summary="glob：缺少有效的 pattern 参数",
            reasons=["pattern 参数缺失或无效"],
        )

    if _is_sensitive_glob_pattern(pattern):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="glob",
            operation="匹配文件路径",
            summary=f"glob：拒绝使用敏感路径模式 '{pattern[:80]}'",
            reasons=[f"glob 模式 '{pattern[:80]}' 指向敏感文件，不允许匹配"],
        )

    return PermissionDecision(
        level=PermissionLevel.SAFE,
        tool_name="glob",
        operation="匹配文件路径",
        summary=f"glob：模式 '{pattern[:80]}'",
        reasons=["glob 为只读操作"],
    )


# ---------------------------------------------------------------------------
# write_file 检查器（预留 — 工具尚未实现）
# ---------------------------------------------------------------------------

@_register_checker("write_file")
def _check_write_file(
    arguments: dict[str, object],
    workspace_root: Path,
) -> PermissionDecision:
    file_path = arguments.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="write_file",
            operation="写入文件",
            summary="write_file：缺少有效的 file_path 参数",
            reasons=["file_path 参数缺失或无效"],
        )

    # 提取 mode 参数，默认 "overwrite"
    mode = arguments.get("mode", "overwrite")
    if not isinstance(mode, str) or mode not in ("overwrite", "append"):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="write_file",
            operation="写入文件",
            summary=f"write_file：无效的 mode 参数 '{mode}'",
            reasons=[f"mode 参数值 '{mode}' 无效，必须为 'overwrite' 或 'append'"],
        )

    target = _resolve_arg_path(file_path, workspace_root)

    if not is_within_workspace(target, workspace_root):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="write_file",
            operation="写入文件",
            summary=f"write_file：拒绝写入工作区外路径 {target}",
            target_paths=[target],
            reasons=[f"路径 {target} 不在工作区 {workspace_root} 范围内"],
        )

    if is_sensitive_file(target):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="write_file",
            operation="写入文件",
            summary=f"write_file：拒绝写入敏感文件 {target}",
            target_paths=[target],
            reasons=[f"{target} 属于敏感文件，不允许写入"],
        )

    # 根据 mode 和文件是否存在判断权限等级
    if target.exists():
        if mode == "append":
            return PermissionDecision(
                level=PermissionLevel.CAUTION,
                tool_name="write_file",
                operation="追加内容",
                summary=f"write_file：追加内容到已有文件 {target}",
                target_paths=[target],
                reasons=[f"追加内容到已有文件 {target}，不破坏原内容"],
            )
        return PermissionDecision(
            level=PermissionLevel.DANGEROUS,
            tool_name="write_file",
            operation="覆盖文件",
            summary=f"write_file：覆盖已有文件 {target}",
            target_paths=[target],
            reasons=[f"{target} 已存在，覆盖写入将替换原内容"],
        )

    return PermissionDecision(
        level=PermissionLevel.CAUTION,
        tool_name="write_file",
        operation="创建文件",
        summary=f"write_file：创建新文件 {target}",
        target_paths=[target],
        reasons=[f"将在 {target} 创建新文件"],
    )


# ---------------------------------------------------------------------------
# edit_file 检查器（预留 — 工具尚未实现）
# ---------------------------------------------------------------------------

@_register_checker("edit_file")
def _check_edit_file(
    arguments: dict[str, object],
    workspace_root: Path,
) -> PermissionDecision:
    file_path = arguments.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="edit_file",
            operation="编辑文件",
            summary="edit_file：缺少有效的 file_path 参数",
            reasons=["file_path 参数缺失或无效"],
        )

    target = _resolve_arg_path(file_path, workspace_root)

    if not is_within_workspace(target, workspace_root):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="edit_file",
            operation="编辑文件",
            summary=f"edit_file：拒绝编辑工作区外路径 {target}",
            target_paths=[target],
            reasons=[f"路径 {target} 不在工作区 {workspace_root} 范围内"],
        )

    if is_sensitive_file(target):
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="edit_file",
            operation="编辑文件",
            summary=f"edit_file：拒绝编辑敏感文件 {target}",
            target_paths=[target],
            reasons=[f"{target} 属于敏感文件，不允许编辑"],
        )

    return PermissionDecision(
        level=PermissionLevel.DANGEROUS,
        tool_name="edit_file",
        operation="编辑文件",
        summary=f"edit_file：修改文件 {target}",
        target_paths=[target],
        reasons=["编辑操作会修改文件内容"],
    )


# ---------------------------------------------------------------------------
# delete_file / remove_file 检查器（预留 — 工具尚未实现）
# ---------------------------------------------------------------------------

def _make_delete_checker(tool_name: str) -> _CheckerFunc:
    """创建 delete_file 或 remove_file 检查器。"""
    def checker(
        arguments: dict[str, object],
        workspace_root: Path,
    ) -> PermissionDecision:
        file_path = arguments.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            return PermissionDecision(
                level=PermissionLevel.DENY,
                tool_name=tool_name,
                operation="删除文件",
                summary=f"{tool_name}：缺少有效的 file_path 参数",
                reasons=["file_path 参数缺失或无效"],
            )

        target = _resolve_arg_path(file_path, workspace_root)

        if not is_within_workspace(target, workspace_root):
            return PermissionDecision(
                level=PermissionLevel.DENY,
                tool_name=tool_name,
                operation="删除文件",
                summary=f"{tool_name}：拒绝删除工作区外路径 {target}",
                target_paths=[target],
                reasons=[f"路径 {target} 不在工作区 {workspace_root} 范围内"],
            )

        if is_sensitive_file(target):
            return PermissionDecision(
                level=PermissionLevel.DENY,
                tool_name=tool_name,
                operation="删除文件",
                summary=f"{tool_name}：拒绝删除敏感文件 {target}",
                target_paths=[target],
                reasons=[f"{target} 属于敏感文件，不允许删除"],
            )

        # 删除 workspace root 本身
        if target == workspace_root:
            return PermissionDecision(
                level=PermissionLevel.DENY,
                tool_name=tool_name,
                operation="删除文件",
                summary=f"{tool_name}：拒绝删除工作区根目录",
                target_paths=[target],
                reasons=["不允许删除工作区根目录"],
            )

        return PermissionDecision(
            level=PermissionLevel.DANGEROUS,
            tool_name=tool_name,
            operation="删除文件",
            summary=f"{tool_name}：删除 {target}",
            target_paths=[target],
            reasons=["删除操作不可撤销"],
        )

    return checker


_CHECKERS["delete_file"] = _make_delete_checker("delete_file")
_CHECKERS["remove_file"] = _make_delete_checker("remove_file")


# ---------------------------------------------------------------------------
# shell 检查器（预留 — 工具尚未实现）
# ---------------------------------------------------------------------------

@_register_checker("shell")
def _check_shell(
    arguments: dict[str, object],
    workspace_root: Path,
) -> PermissionDecision:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return PermissionDecision(
            level=PermissionLevel.DENY,
            tool_name="shell",
            operation="执行命令",
            summary="shell：缺少有效的 command 参数",
            reasons=["command 参数缺失或无效"],
        )

    command_stripped = command.strip()

    # 拒绝级危险命令
    for pattern in _DENY_SHELL_PATTERNS:
        if pattern.search(command_stripped):
            return PermissionDecision(
                level=PermissionLevel.DENY,
                tool_name="shell",
                operation="执行命令",
                summary="shell：检测到高危操作，已拒绝执行",
                reasons=[f"命令包含高危模式：{pattern.pattern}"],
            )

    # 危险级命令
    for pattern in _DANGEROUS_SHELL_PATTERNS:
        if pattern.search(command_stripped):
            return PermissionDecision(
                level=PermissionLevel.DANGEROUS,
                tool_name="shell",
                operation="执行命令",
                summary=f"shell：{command_stripped[:120]}",
                reasons=[f"命令包含危险模式：{pattern.pattern}"],
            )

    # 默认：shell 命令均需确认
    return PermissionDecision(
        level=PermissionLevel.DANGEROUS,
        tool_name="shell",
        operation="执行命令",
        summary=f"shell：{command_stripped[:120]}",
        reasons=["执行 shell 命令需要确认"],
    )


# ---------------------------------------------------------------------------
# 未知工具检查器
# ---------------------------------------------------------------------------

def _make_unknown_checker(tool_name: str) -> _CheckerFunc:
    """为未注册的工具生成检查器 — 默认返回 dangerous。"""

    def checker(
        arguments: dict[str, object],
        workspace_root: Path,
    ) -> PermissionDecision:
        return PermissionDecision(
            level=PermissionLevel.DANGEROUS,
            tool_name=tool_name,
            operation="未知操作",
            summary=f"{tool_name}：未注册的工具，默认需要确认",
            reasons=[f"工具 '{tool_name}' 不在权限规则白名单中"],
        )

    return checker
