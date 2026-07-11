"""记忆系统配置测试。

覆盖以下场景：
- MemoryConfig 默认值
- MemoryConfig 自定义值
- AppConfig 包含 memory 属性
"""

from minicode.config.models import AppConfig, MemoryConfig


class TestMemoryConfig:
    """MemoryConfig 模型测试。"""

    def test_default_values(self) -> None:
        """验证默认值正确。"""
        cfg = MemoryConfig()
        assert cfg.enabled is True
        assert cfg.max_chars == 8000

    def test_custom_values(self) -> None:
        """验证自定义值正确。"""
        cfg = MemoryConfig(enabled=False, max_chars=4000)
        assert cfg.enabled is False
        assert cfg.max_chars == 4000

    def test_max_chars_zero(self) -> None:
        """验证 max_chars 可以为 0（禁用记忆内容注入）。"""
        cfg = MemoryConfig(max_chars=0)
        assert cfg.max_chars == 0

    def test_app_config_contains_memory(self) -> None:
        """验证 AppConfig 包含 memory 属性且默认值正确。"""
        cfg = AppConfig()
        assert hasattr(cfg, "memory")
        assert isinstance(cfg.memory, MemoryConfig)
        assert cfg.memory.enabled is True
        assert cfg.memory.max_chars == 8000

    def test_app_config_custom_memory(self) -> None:
        """验证 AppConfig 支持自定义 memory 配置。"""
        memory = MemoryConfig(enabled=False, max_chars=2000)
        cfg = AppConfig(memory=memory)
        assert cfg.memory.enabled is False
        assert cfg.memory.max_chars == 2000
