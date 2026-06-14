#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket 连接管理 — 广播实时测试状态到浏览器
"""
import asyncio
import json
import logging
from typing import Set
from fastapi import WebSocket

logger = logging.getLogger('web.ws_manager')


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info(f"WebSocket 客户端已连接 (当前: {len(self._connections)})")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections.discard(ws)
        logger.info(f"WebSocket 客户端断开 (剩余: {len(self._connections)})")

    async def broadcast(self, message: dict):
        """向所有已连接客户端广播 JSON 消息"""
        dead = set()
        async with self._lock:
            for ws in self._connections:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


# 全局单例
manager = ConnectionManager()
