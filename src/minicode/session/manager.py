"""会话管理器。

提供会话的 CRUD 操作和索引管理。
所有 I/O 错误采用 fail-soft 策略：记录日志但不抛出异常。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from minicode.providers.base import ContentBlock, Message
from minicode.session.models import Session, deserialize_messages, serialize_messages
from minicode.utils.log import get_logger

logger = get_logger(__name__)


def _message_text(message: Message) -> str:
    """提取消息中的纯文本内容并去除首尾空白。"""
    content = message.content
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    return "".join(
        block.text
        for block in content
        if block.type == "text" and block.text
    ).strip()


def _summarize_text(text: str) -> str:
    """应用会话列表统一的空概要与 15 字截断规则。"""
    if not text:
        return "（无概要）"
    if len(text) <= 15:
        return text
    return text[:15] + "..."


def summarize_user_input(content: str | list[ContentBlock] | None) -> str:
    """将用户输入转换为稳定的会话列表概要。"""
    return _summarize_text(_message_text(Message(role="user", content=content)))


class SessionManager:
    """会话管理器。

    负责会话的创建、保存、加载、列表和删除。
    会话数据存储在 .minicode/sessions/ 目录下：
    - index.json：所有会话的摘要索引
    - {session_id}.json：单个会话的完整数据

    用法：
        manager = SessionManager(workspace_root)
        session = manager.create(model="deepseek-v4-flash", provider="deepseek")
        manager.save(session)
        sessions = manager.list_sessions()
        loaded = manager.load(session.id)
        manager.delete(session.id)

    """

    def __init__(self, workspace_root: Path) -> None:
        """初始化会话管理器。

        Args:
            workspace_root: 工作区根路径，.minicode/sessions/ 将创建在此目录下。
        """
        self._workspace_root = workspace_root
        self._sessions_dir = workspace_root / ".minicode" / "sessions"
        self._index_path = self._sessions_dir / "index.json"

    # ─── 公开 API ─────────────────────────────────────────────

    def create(
        self,
        model: str = "",
        provider: str = "",
        workspace_root: str = "",
    ) -> Session:
        """创建新会话。

        Args:
            model: 当前使用的模型名称。
            provider: 当前使用的 Provider 名称。
            workspace_root: 工作区路径字符串。

        Returns:
            新创建的 Session 实例。
        """
        now = datetime.now(UTC)
        session = Session(
            model=model,
            provider=provider,
            workspace_root=workspace_root,
            created_at=now,
            updated_at=now,
        )
        # 使用创建时间生成可读名称
        session.name = now.strftime("%Y-%m-%d %H:%M")
        return session

    def save(self, session: Session) -> None:
        """保存会话到磁盘。

        1. 确保 sessions 目录存在
        2. 将 Session 序列化为 JSON，写入 {id}.json
        3. 更新 index.json

        I/O 错误会被记录但不抛出，保证不阻断对话流程。

        Args:
            session: 要保存的 Session 实例。
        """
        try:
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("无法创建会话目录", path=str(self._sessions_dir), error=str(e))
            return

        # 写入会话文件
        session_path = self._session_path(session.id)
        try:
            session_data = session.model_dump(mode="json")
            # messages 字段使用自定义序列化以确保 ToolMessage 正确序列化
            session_data["messages"] = serialize_messages(session.messages)
            session_path.write_text(
                json.dumps(session_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("会话已保存", session_id=session.id, path=str(session_path))
        except (OSError, TypeError, ValueError) as e:
            logger.warning("保存会话文件失败", session_id=session.id, error=str(e))
            return

        # 更新索引
        self._update_index(session)

    def load(self, session_id: str) -> Session | None:
        """从磁盘加载指定会话。

        如果会话文件不存在或损坏，返回 None 并记录日志。

        Args:
            session_id: 会话 ID。

        Returns:
            加载的 Session 实例，失败时返回 None。
        """
        session_path = self._session_path(session_id)
        if not session_path.exists():
            logger.warning("会话文件不存在", session_id=session_id, path=str(session_path))
            return None

        try:
            raw = json.loads(session_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("会话文件读取或解析失败", session_id=session_id, error=str(e))
            return None

        try:
            # 反序列化 messages（处理 ToolMessage 子类）
            raw["messages"] = deserialize_messages(raw.get("messages", []))
            session = Session(**raw)
            logger.debug("会话已加载", session_id=session_id, message_count=session.message_count)
            return session
        except Exception as e:
            logger.warning("会话数据反序列化失败", session_id=session_id, error=str(e))
            return None

    def list_sessions(self) -> list[dict]:
        """列出所有会话的摘要信息。

        从 index.json 读取，无需加载完整会话文件。
        按 updated_at 降序排列（最近更新的在前）。

        Returns:
            会话摘要列表，每个元素包含 id/name/summary/created_at/updated_at/
            model/provider/message_count。
            如果索引文件不存在或损坏，返回空列表。
        """
        index = self._load_index()
        # 按 updated_at 降序
        index.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return index

    def delete(self, session_id: str) -> bool:
        """删除指定会话及其索引条目。

        返回值语义：
        - True：会话文件已删除（无论索引中是否存在该条目）
        - True：文件不存在但索引中存在孤儿条目（已清理）
        - False：文件不存在且索引中也不存在该会话
        - False：删除文件时发生 I/O 错误

        Args:
            session_id: 要删除的会话 ID。

        Returns:
            是否确有数据被清除。
        """
        session_path = self._session_path(session_id)
        file_deleted = False

        # 删除会话文件
        if session_path.exists():
            try:
                session_path.unlink()
                file_deleted = True
                logger.debug("会话文件已删除", session_id=session_id)
            except OSError as e:
                logger.warning("删除会话文件失败", session_id=session_id, error=str(e))
                return False
        else:
            logger.debug("会话文件不存在", session_id=session_id)

        # 从索引中移除
        index_cleaned = self._remove_from_index(session_id)

        # 有实质操作（删了文件或清了索引）才算成功
        return file_deleted or index_cleaned

    # ─── 内部方法 ─────────────────────────────────────────────

    @staticmethod
    def _compute_summary(session: Session) -> str:
        """从会话消息中提取概要。

        找到第一条 role == "user" 的消息，取其内容的前 15 个字符作为概要。

        Args:
            session: 会话实例。

        Returns:
            概要字符串，超过 15 个字符时截断并追加 "..."。
        """
        initial_summary = session.metadata.get("initial_user_summary")
        if isinstance(initial_summary, str) and initial_summary.strip():
            return initial_summary.strip()

        for message in session.messages:
            if message.role != "user" or message.kind == "compact_summary":
                continue
            return _summarize_text(_message_text(message))
        return "（无概要）"

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        """验证 session_id 格式安全，防止路径穿越。

        要求：
        - 必须是 32 位小写十六进制字符串（uuid4().hex 格式）
        - 不含路径分隔符、..、绝对路径等危险字符

        Args:
            session_id: 待验证的会话 ID。

        Raises:
            ValueError: 格式无效时抛出。
        """
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id 必须是字符串且不能为空")
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("session_id 必须是32位小写十六进制字符串")

    def _session_path(self, session_id: str) -> Path:
        """获取指定会话的文件路径（带安全校验）。

        验证 session_id 格式后构造路径，并确保 resolve() 后的路径
        仍位于 .minicode/sessions/ 目录内，防止路径穿越攻击。

        Args:
            session_id: 会话 ID。

        Returns:
            resolve() 后的安全 Path 对象。

        Raises:
            ValueError: session_id 格式无效或路径穿越检测失败。
        """
        self._validate_session_id(session_id)
        path = (self._sessions_dir / f"{session_id}.json").resolve()
        expected_prefix = str(self._sessions_dir.resolve()).rstrip("\\/")
        if not str(path).startswith(expected_prefix):
            raise ValueError(f"路径穿越检测：{session_id}")
        return path

    def _load_index(self) -> list[dict]:
        """加载索引文件。

        Returns:
            索引数据列表。文件不存在或损坏时返回空列表。
        """
        if not self._index_path.exists():
            return []
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            logger.warning("索引文件格式异常（非数组），将重建索引")
            return []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("索引文件读取失败", error=str(e))
            return []

    def _save_index(self, data: list[dict]) -> None:
        """写入索引文件。"""
        try:
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
            self._index_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("索引文件写入失败", error=str(e))

    def _update_index(self, session: Session) -> None:
        """更新索引中指定会话的条目（不存在则新增）。"""
        index = self._load_index()
        summary = {
            "id": session.id,
            "name": session.name,
            "summary": self._compute_summary(session),
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "model": session.model,
            "provider": session.provider,
            "message_count": session.message_count,
        }

        # 查找并更新或追加
        for i, entry in enumerate(index):
            if entry.get("id") == session.id:
                index[i] = summary
                break
        else:
            index.append(summary)

        self._save_index(index)

    def _remove_from_index(self, session_id: str) -> bool:
        """从索引中移除指定会话。

        Args:
            session_id: 要移除的会话 ID。

        Returns:
            True 表示索引中原本包含该会话并已移除。
        """
        index = self._load_index()
        original_len = len(index)
        index = [entry for entry in index if entry.get("id") != session_id]
        self._save_index(index)
        return len(index) < original_len
