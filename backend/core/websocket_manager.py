# -*- coding: utf-8 -*-
# WebSocket 连接管理器

"""
WebSocket 管理器：处理前端实时进度推送。

功能：
- 管理多个客户端的 WebSocket 连接
- 推送扫描进度到指定客户端
- 支持任务取消
"""

import asyncio
import json
from typing import Dict, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect

logger = None


def _get_logger():
    """延迟获取 logger"""
    global logger
    if logger is None:
        from utils.logger import get_logger
        logger = get_logger()
    return logger


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # 活跃连接: {client_id: set(websocket)}
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # 扫描任务状态: {task_id: {"cancelled": bool, "client_id": str}}
        self.scan_tasks: Dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, client_id: str = "default"):
        """
        连接 WebSocket

        Args:
            websocket: WebSocket 连接
            client_id: 客户端标识
        """
        await websocket.accept()
        if client_id not in self.active_connections:
            self.active_connections[client_id] = set()
        self.active_connections[client_id].add(websocket)
        _get_logger().info(f"WebSocket 连接: client_id={client_id}")

    def disconnect(self, websocket: WebSocket, client_id: str = "default"):
        """
        断开 WebSocket

        Args:
            websocket: WebSocket 连接
            client_id: 客户端标识
        """
        if client_id in self.active_connections:
            self.active_connections[client_id].discard(websocket)
            if not self.active_connections[client_id]:
                del self.active_connections[client_id]
        _get_logger().info(f"WebSocket 断开: client_id={client_id}")

    async def broadcast(self, message: dict, client_id: str = "default"):
        """
        广播消息到指定客户端的所有连接

        Args:
            message: 消息内容
            client_id: 客户端标识
        """
        if client_id not in self.active_connections:
            return

        disconnected = set()
        for connection in self.active_connections[client_id]:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)

        # 清理断开的连接
        for conn in disconnected:
            self.active_connections[client_id].discard(conn)

    async def send_to_all(self, message: dict):
        """
        广播消息到所有客户端

        Args:
            message: 消息内容
        """
        for client_id in list(self.active_connections.keys()):
            await self.broadcast(message, client_id)

    async def send_scan_progress(
        self,
        client_id: str,
        task_id: str,
        scanned: int,
        total: int,
        current_file: str = "",
        status: str = "scanning"
    ):
        """
        发送扫描进度

        Args:
            client_id: 客户端标识
            task_id: 任务ID
            scanned: 已扫描数量
            total: 总数
            current_file: 当前处理的文件名
            status: 状态
        """
        progress = round(scanned / total * 100, 1) if total > 0 else 0
        message = {
            "type": "scan_progress",
            "task_id": task_id,
            "data": {
                "scanned": scanned,
                "total": total,
                "current_file": current_file,
                "status": status,
                "progress": progress
            }
        }
        await self.broadcast(message, client_id)

    async def send_scan_complete(
        self,
        client_id: str,
        task_id: str,
        total: int,
        added: int,
        skipped: int,
        error: Optional[str] = None
    ):
        """
        发送扫描完成消息

        Args:
            client_id: 客户端标识
            task_id: 任务ID
            total: 总数
            added: 新增数量
            skipped: 跳过数量
            error: 错误信息
        """
        message = {
            "type": "scan_complete",
            "task_id": task_id,
            "data": {
                "total": total,
                "added": added,
                "skipped": skipped,
                "error": error,
                "status": "complete" if not error else "error"
            }
        }
        await self.broadcast(message, client_id)

    async def send_scan_error(
        self,
        client_id: str,
        task_id: str,
        error: str
    ):
        """
        发送扫描错误消息

        Args:
            client_id: 客户端标识
            task_id: 任务ID
            error: 错误信息
        """
        message = {
            "type": "scan_error",
            "task_id": task_id,
            "data": {
                "error": error,
                "status": "error"
            }
        }
        await self.broadcast(message, client_id)

    async def send_scan_status(
        self,
        client_id: str,
        task_id: str,
        status: str,
        message: str = ""
    ):
        """
        发送扫描状态消息

        Args:
            client_id: 客户端标识
            task_id: 任务ID
            status: 状态
            message: 状态消息
        """
        msg = {
            "type": "scan_status",
            "task_id": task_id,
            "data": {
                "status": status,
                "message": message
            }
        }
        await self.broadcast(msg, client_id)

    async def send_scan_log(
        self,
        client_id: str,
        task_id: str,
        log_type: str,
        message: str,
        data: Optional[dict] = None
    ):
        """
        发送扫描日志消息（用于调试）

        Args:
            client_id: 客户端标识
            task_id: 任务ID
            log_type: 日志类型 (info/warning/error/debug)
            message: 日志消息
            data: 附加数据
        """
        msg = {
            "type": "scan_log",
            "task_id": task_id,
            "data": {
                "log_type": log_type,
                "message": message,
                "data": data or {},
                "timestamp": asyncio.get_event_loop().time()
            }
        }
        await self.broadcast(msg, client_id)

    def is_task_cancelled(self, task_id: str) -> bool:
        """
        检查任务是否已取消

        Args:
            task_id: 任务ID

        Returns:
            是否已取消
        """
        return self.scan_tasks.get(task_id, {}).get("cancelled", False)

    def cancel_task(self, task_id: str):
        """
        取消任务

        Args:
            task_id: 任务ID
        """
        if task_id in self.scan_tasks:
            self.scan_tasks[task_id]["cancelled"] = True

    def register_task(self, task_id: str, client_id: str):
        """
        注册任务

        Args:
            task_id: 任务ID
            client_id: 客户端标识
        """
        self.scan_tasks[task_id] = {
            "cancelled": False,
            "client_id": client_id
        }

    def unregister_task(self, task_id: str):
        """
        注销任务

        Args:
            task_id: 任务ID
        """
        if task_id in self.scan_tasks:
            del self.scan_tasks[task_id]

    def get_connection_count(self) -> int:
        """
        获取总连接数

        Returns:
            连接数
        """
        return sum(len(conns) for conns in self.active_connections.values())

    async def handle_client_message(
        self,
        websocket: WebSocket,
        client_id: str
    ):
        """
        处理客户端消息

        Args:
            websocket: WebSocket 连接
            client_id: 客户端标识
        """
        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                msg_type = message.get("type", "")

                if msg_type == "cancel":
                    task_id = message.get("task_id")
                    if task_id:
                        self.cancel_task(task_id)
                        _get_logger().info(f"任务已取消: {task_id}")

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

        except WebSocketDisconnect:
            self.disconnect(websocket, client_id)
        except json.JSONDecodeError:
            _get_logger().warning(f"无效的 JSON 消息 from {client_id}")
        except Exception as e:
            _get_logger().error(f"处理客户端消息失败: {e}")
            self.disconnect(websocket, client_id)


# ========== 全局单例 ==========

_ws_manager: Optional[ConnectionManager] = None


def get_ws_manager() -> ConnectionManager:
    """
    获取 WebSocket 管理器单例

    Returns:
        ConnectionManager 实例
    """
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = ConnectionManager()
    return _ws_manager


def reset_ws_manager():
    """重置 WebSocket 管理器"""
    global _ws_manager
    _ws_manager = None
