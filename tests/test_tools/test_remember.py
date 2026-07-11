"""remember 工具测试。

覆盖以下场景：
- 正常添加记忆
- 非法名称被拒绝
- 空参数被拒绝
- 敏感信息检测
- workspace scope 默认值
- global scope 支持
- workspace_root 未设置时拒绝
- 与 MemoryManager 集成的校验
"""
from __future__ import annotations

from pathlib import Path

import pytest

from minicode.memory.manager import MemoryManager
from minicode.tools.remember import Remember

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tool() -> Remember:
    t = Remember()
    return t


@pytest.fixture
def tool_with_root(tmp_path: Path) -> Remember:
    t = Remember()
    t.workspace_root = tmp_path
    return t


class TestRememberTool:
    """remember 工具基本功能测试。"""

    async def test_remember_normal(self, tool_with_root: Remember) -> None:
        """正常添加记忆。"""
        result = await tool_with_root.execute(
            name="reply-language",
            content="用户喜欢用中文回答",
            description="回复语言偏好",
        )
        assert result.success
        assert "已记住" in result.output

        # 验证实际写入
        manager = MemoryManager(tool_with_root.workspace_root)  # type: ignore[arg-type]
        memory = manager.get("reply-language")
        assert memory is not None
        assert "用户喜欢用中文回答" in memory.content
        assert memory.metadata.description == "回复语言偏好"

    async def test_remember_with_workspace_scope(self, tool_with_root: Remember) -> None:
        """默认使用 workspace scope。"""
        result = await tool_with_root.execute(
            name="project-preference",
            content="这个项目默认使用 pytest",
        )
        assert result.success

        manager = MemoryManager(tool_with_root.workspace_root)  # type: ignore[arg-type]
        memory = manager.get("project-preference")
        assert memory is not None
        assert memory.metadata.scope.value == "workspace"

    async def test_remember_with_global_scope(self, tool_with_root: Remember) -> None:
        """支持 global scope。"""
        result = await tool_with_root.execute(
            name="global-preference",
            content="用户喜欢英文回复",
            scope="global",
            type="user",
        )
        assert result.success

        manager = MemoryManager(tool_with_root.workspace_root)  # type: ignore[arg-type]
        memory = manager.get("global-preference")
        assert memory is not None
        assert memory.metadata.scope.value == "global"
        assert memory.metadata.type.value == "user"
        assert memory.metadata.confidence == 0.9  # 默认置信度

    async def test_remember_invalid_name(self, tool_with_root: Remember) -> None:
        """非法名称被拒绝。"""
        result = await tool_with_root.execute(
            name="../escape-path",
            content="要保存的内容",
        )
        assert not result.success
        assert "包含非法字符" in result.output

    async def test_remember_invalid_name_with_spaces(self, tool_with_root: Remember) -> None:
        """带空格的名称被拒绝。"""
        result = await tool_with_root.execute(
            name="my memory",
            content="内容",
        )
        assert not result.success
        assert "包含非法字符" in result.output

    async def test_remember_empty_name(self, tool: Remember) -> None:
        """空的 name 被拒绝。"""
        result = await tool.execute(
            name="",
            content="内容",
        )
        assert not result.success
        assert "name 必须是有效的非空字符串" in result.output

    async def test_remember_empty_content(self, tool_with_root: Remember) -> None:
        """空的 content 被拒绝。"""
        result = await tool_with_root.execute(
            name="test",
            content="",
        )
        assert not result.success
        assert "content 必须是有效的非空字符串" in result.output

    async def test_remember_no_workspace_root(self, tool: Remember) -> None:
        """未设置 workspace_root 时拒绝。"""
        result = await tool.execute(
            name="test-memory",
            content="测试内容",
        )
        assert not result.success
        assert "工作区根路径未设置" in result.output

    async def test_sensitive_info_password_rejected(self, tool_with_root: Remember) -> None:
        """密码被拒绝。"""
        result = await tool_with_root.execute(
            name="my-password",
            content="my password is abc123",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_sensitive_info_token_rejected(self, tool_with_root: Remember) -> None:
        """token 被拒绝。"""
        result = await tool_with_root.execute(
            name="api-token",
            content="API token 是 sk-xxxxx",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_sensitive_info_api_key_rejected(self, tool_with_root: Remember) -> None:
        """API key 被拒绝。"""
        result = await tool_with_root.execute(
            name="openai-key",
            content="我的 OpenAI API key 是 sk-xxx",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_sensitive_info_secret_rejected(self, tool_with_root: Remember) -> None:
        """secret 被拒绝。"""
        result = await tool_with_root.execute(
            name="db-secret",
            content="数据库 secret 是 xyz",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_sensitive_info_private_key_rejected(self, tool_with_root: Remember) -> None:
        """private key 被拒绝。"""
        result = await tool_with_root.execute(
            name="ssh-key",
            content="我的 SSH private key 是 xyz",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_confidence_custom_value(self, tool_with_root: Remember) -> None:
        """支持自定义置信度。"""
        result = await tool_with_root.execute(
            name="custom-conf",
            content="自定义置信度测试",
            confidence=0.5,
        )
        assert result.success

        manager = MemoryManager(tool_with_root.workspace_root)  # type: ignore[arg-type]
        memory = manager.get("custom-conf")
        assert memory is not None
        assert memory.metadata.confidence == 0.5

    async def test_source_is_user(self, tool_with_root: Remember) -> None:
        """记忆来源应为 USER。"""
        result = await tool_with_root.execute(
            name="source-test",
            content="来源测试",
        )
        assert result.success

        manager = MemoryManager(tool_with_root.workspace_root)  # type: ignore[arg-type]
        memory = manager.get("source-test")
        assert memory is not None
        assert memory.metadata.source.value == "user"

    async def test_invalid_scope(self, tool_with_root: Remember) -> None:
        """无效的 scope 被拒绝。"""
        result = await tool_with_root.execute(
            name="test",
            content="内容",
            scope="invalid",
        )
        assert not result.success
        assert "无效的 scope" in result.output

    async def test_invalid_type(self, tool_with_root: Remember) -> None:
        """无效的 type 被拒绝。"""
        result = await tool_with_root.execute(
            name="test",
            content="内容",
            type="invalid",
        )
        assert not result.success
        assert "无效的 type" in result.output

    async def test_long_name_with_hyphens(self, tool_with_root: Remember) -> None:
        """包含连字符和下划线的名称应被接受。"""
        result = await tool_with_root.execute(
            name="user-preference_reply-language",
            content="用户偏好测试",
        )
        assert result.success

        manager = MemoryManager(tool_with_root.workspace_root)  # type: ignore[arg-type]
        memory = manager.get("user-preference_reply-language")
        assert memory is not None


class TestRememberChineseSensitive:
    """中文敏感信息检测测试。"""

    async def test_chinese_password_in_content(self, tool_with_root: Remember) -> None:
        """中文「密码」在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="my-note",
            content="我的密码是 abc123",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_chinese_miyao_in_content(self, tool_with_root: Remember) -> None:
        """中文「密钥」在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="my-key",
            content="这是项目密钥 xyz",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_chinese_siyao_in_content(self, tool_with_root: Remember) -> None:
        """中文「私钥」在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="ssh-key",
            content="我的 SSH 私钥是 xxx",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_chinese_lingpai_in_content(self, tool_with_root: Remember) -> None:
        """中文「令牌」在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="my-token",
            content="访问令牌已更新",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_chinese_pingju_in_content(self, tool_with_root: Remember) -> None:
        """中文「凭据」在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="cred",
            content="数据库凭据已更新",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_chinese_api_key_in_content(self, tool_with_root: Remember) -> None:
        """中文「API密钥」在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="api-key",
            content="API密钥是 xxx",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_chinese_kouling_in_content(self, tool_with_root: Remember) -> None:
        """中文「口令」在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="my-pass",
            content="登录口令已更新",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_chinese_password_in_name(self, tool_with_root: Remember) -> None:
        """英文敏感关键词在 name 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="my-secret-note",
            content="这是一个普通笔记",
        )
        assert not result.success
        assert "拒绝保存" in result.output
        assert "敏感关键词" in result.output

    async def test_chinese_keyword_in_description(self, tool_with_root: Remember) -> None:
        """中文「密钥」在 description 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="my-note",
            content="普通笔记内容",
            description="这是项目密钥信息",
        )
        assert not result.success
        assert "拒绝保存" in result.output


class TestRememberKeyPatterns:
    """敏感密钥模式检测测试。"""

    async def test_openai_sk_pattern_rejected(self, tool_with_root: Remember) -> None:
        """OpenAI 风格 sk-xxx 在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="openai-key",
            content="这是我的 key sk-proj-abc123def456",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_github_ghp_pattern_rejected(self, tool_with_root: Remember) -> None:
        """GitHub ghp_xxx 在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="github-token",
            content="github token is ghp_abc123def456xyz7890123456",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_github_pat_pattern_rejected(self, tool_with_root: Remember) -> None:
        """GitHub github_pat_xxx 在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="github-pat",
            content="pat: github_pat_abc123def456xyz78901",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_pem_private_key_rejected(self, tool_with_root: Remember) -> None:
        """PEM 私钥头在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="pem-key",
            content="-----BEGIN PRIVATE KEY-----\nbase64data",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_aws_akia_pattern_rejected(self, tool_with_root: Remember) -> None:
        """AWS AKIA 密钥在 content 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="aws-key",
            content="AKIA1234567890ABCDEF",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_sk_pattern_in_description(self, tool_with_root: Remember) -> None:
        """sk-xxx 模式在 description 中应被拒绝。"""
        result = await tool_with_root.execute(
            name="my-key",
            content="普通内容",
            description="API key: sk-proj-test12345",
        )
        assert not result.success
        assert "拒绝保存" in result.output

    async def test_normal_content_not_rejected_by_patterns(self, tool_with_root: Remember) -> None:
        """合法内容不因模式检查误拒。"""
        result = await tool_with_root.execute(
            name="skill-note",
            content="用户擅长使用 Python 和 shell 脚本",
        )
        assert result.success
        assert "已记住" in result.output
