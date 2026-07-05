"""Shared pytest configuration for MiniCode tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def clean_minicode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除所有 MINICODE_* 环境变量，避免本机真实配置干扰测试。"""
    for key in list(os.environ.keys()):
        if key.startswith("MINICODE_"):
            monkeypatch.delenv(key, raising=False)
