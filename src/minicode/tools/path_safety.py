"""路径安全检查工具。

提供 workspace 越界检查和敏感文件检测功能。
所有路径工具在执行前必须先通过安全检查。
"""

from __future__ import annotations

from pathlib import Path

from minicode.utils.exceptions import ToolError

# 敏感文件名或路径片段（大小写不敏感匹配）
SENSITIVE_FILE_PARTS: frozenset[str] = frozenset({
    # 通用密钥/凭证文件
    ".env",
    ".envrc",
    # SSH 相关
    ".ssh",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ecdsa_sk",
    "id_ed25519",
    "id_ed25519_sk",
    "authorized_keys",
    "known_hosts",
    # 证书与私钥
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".der",
    ".cert",
    ".crt",
    ".ca-bundle",
    # 凭证配置
    ".netrc",
    ".gitconfig",
    ".git-credentials",
    "credentials",
    "credentials.json",
    "credentials.yml",
    "credentials.yaml",
    # 令牌与密钥
    "token",
    "tokens",
    "secret",
    "secrets",
    # 云服务商凭证
    "service-account",
    "service_account",
    "application_default_credentials",
    # 密码管理器
    ".password-store",
    ".keepass",
})


def resolve_and_validate_path(
    path: str | Path,
    workspace_root: Path,
    check_sensitive: bool = True,
) -> Path:
    """解析路径并进行安全检查。

    1. 将相对路径或绝对路径解析为绝对路径
    2. 验证路径在 workspace_root 范围内
    3. 可选：检查是否为敏感文件

    Args:
        path: 用户提供的路径（相对或绝对）
        workspace_root: 工作区根目录（必须是绝对路径）
        check_sensitive: 是否检查敏感文件，默认 True

    Returns:
        规范化后的绝对路径

    Raises:
        ToolError: 路径越界或访问敏感文件时抛出
    """
    workspace_root = workspace_root.resolve()
    path_obj = Path(path)
    target = path_obj.resolve() if path_obj.is_absolute() else (workspace_root / path).resolve()

    # 检查是否在 workspace 内
    if not _is_within_workspace(target, workspace_root):
        raise ToolError(
            f"路径越界：{target} 不在工作区 {workspace_root} 范围内。"
            "工具只能访问工作区内的文件。"
        )

    # 检查敏感文件
    if check_sensitive and _is_sensitive_file(target):
        raise ToolError(
            f"拒绝访问：{target} 属于敏感文件，"
            "默认情况下不允许读取。"
        )

    return target


def _is_within_workspace(target: Path, workspace_root: Path) -> bool:
    """检查目标路径是否在 workspace 范围内。"""
    try:
        target.resolve().relative_to(workspace_root.resolve())
        return True
    except ValueError:
        return False


def _is_sensitive_file(file_path: Path) -> bool:
    """检查是否为敏感文件。"""
    resolved = file_path.resolve()
    # 拆分路径为各部分（小写）
    parts = [p.lower() for p in resolved.parts]
    name = resolved.name.lower()
    stem = resolved.stem.lower()

    # 检查完整文件名是否匹配
    if name in SENSITIVE_FILE_PARTS:
        return True

    # 检查文件名（无扩展名）是否匹配
    # 例如 "service-account.json" 的 stem 是 "service-account"
    if stem in SENSITIVE_FILE_PARTS:
        return True

    # 检查路径中任意部分是否包含敏感片段
    for part in parts:
        if part in SENSITIVE_FILE_PARTS:
            return True

    # 检查文件扩展名（如 .pem, .key）
    suffix = resolved.suffix.lower()
    return suffix in SENSITIVE_FILE_PARTS


def is_within_workspace(target: Path, workspace_root: Path) -> bool:
    """公开版：检查目标路径是否在 workspace 范围内。"""
    return _is_within_workspace(target, workspace_root)


def is_sensitive_file(file_path: Path) -> bool:
    """公开版：检查是否为敏感文件。"""
    return _is_sensitive_file(file_path)
