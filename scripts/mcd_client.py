#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcd_client.py — 麦当劳中国 MCP Server 极简客户端（仅依赖 Python 标准库）

用途：
  - 封装 Streamable HTTP + JSON-RPC 2.0 握手流程
  - 列出可用工具 / 调用工具
  - Token 从环境变量 MCD_MCP_TOKEN 读取，绝不写入代码库

用法：
  import os, json
  from mcd_client import McdMcpClient
  c = McdMcpClient(os.environ["MCD_MCP_TOKEN"])
  print([t["name"] for t in c.list_tools()])
"""

import json
import os
import urllib.request
import urllib.error

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
# 本地 WorkBuddy 配置文件路径（仅用于免去每次手动 export 环境变量）
LOCAL_MCP_JSON = os.path.expanduser("~/.workbuddy/mcp.json")


def _token_from_local_mcp_json():
    """
    从本机 WorkBuddy 的 mcp.json 里读取 mcd-mcp 的 Bearer Token。

    这样使用者不必每次都 export 环境变量，同时避免在项目里再存一份密钥。
    该文件位于用户主目录，不属于项目产物，也不会被提交到仓库。
    """
    try:
        with open(LOCAL_MCP_JSON, "r", encoding="utf-8") as f:
            conf = json.load(f)
        auth = (conf.get("mcpServers", {})
                    .get("mcd-mcp", {})
                    .get("headers", {})
                    .get("Authorization", ""))
        if auth.startswith("Bearer "):
            return auth[7:].strip()
    except Exception:
        pass
    return ""


class McdMcpError(RuntimeError):
    """MCP 调用异常（含 401/429 等错误码）"""


class McdMcpClient:
    def __init__(self, token=None, url=None, timeout=30, verbose=False):
        # 优先级：显式传入 > 环境变量 > 本机 WorkBuddy mcp.json
        self.token = (token or os.environ.get("MCD_MCP_TOKEN")
                      or _token_from_local_mcp_json())
        if not self.token:
            raise McdMcpError(
                "未找到 MCP Token。请设置环境变量 MCD_MCP_TOKEN，"
                "或在 https://open.mcd.cn/mcp 控制台申请。"
            )
        self.url = url or os.environ.get("MCD_MCP_URL", DEFAULT_URL)
        self.timeout = timeout
        self.verbose = verbose
        self.session_id = None
        self._id = 0
        self.server_info = None

    # ---------- 底层传输 ----------

    def _next_id(self):
        self._id += 1
        return self._id

    def _post(self, payload):
        """发送一次 JSON-RPC 请求，返回解析后的 JSON（兼容 SSE 响应体）。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("Authorization", "Bearer %s" % self.token)
        if self.session_id:
            req.add_header("Mcp-Session-Id", self.session_id)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            hint = {401: "Token 无效/过期，请到 open.mcd.cn/mcp 重新激活",
                    429: "触发限流（600 次/分钟），稍后再试"}.get(e.code, "")
            raise McdMcpError("HTTP %s %s %s" % (e.code, e.reason, hint)) from e
        except urllib.error.URLError as e:
            raise McdMcpError("网络连接失败：%s" % e.reason) from e

        if not raw.strip():
            return None

        # Streamable HTTP 可能返回 SSE 格式：event: message\ndata: {...}
        if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
            for line in raw.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    break
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise McdMcpError("响应解析失败：%s" % raw[:300])

    def _rpc(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            payload["id"] = self._next_id()

        data = self._post(payload)
        if data is None:
            return None
        if "error" in data:
            err = data["error"]
            raise McdMcpError("%s 调用失败：%s" % (method, err.get("message", err)))
        return data.get("result")

    # ---------- MCP 标准方法 ----------

    def initialize(self):
        """握手并完成 initialized 通知。"""
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcd-order-copilot", "version": "0.1.0"},
        })
        self.server_info = (result or {}).get("serverInfo", {})
        self._rpc("notifications/initialized", {}, notify=True)
        return self.server_info

    def list_tools(self):
        """返回工具列表（含 name / description / inputSchema）。"""
        if self.session_id is None:
            self.initialize()
        result = self._rpc("tools/list", {})
        return (result or {}).get("tools", [])

    def call_tool(self, name, arguments=None):
        """调用工具，返回解析后的 JSON 内容（失败时抛 McdMcpError）。"""
        if self.session_id is None:
            self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result is None:
            raise McdMcpError("工具 %s 无响应" % name)

        # 结构化内容优先
        structured = result.get("structuredContent")
        if structured:
            return structured

        text_parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        raw = "\n".join(text_parts).strip()
        if not raw:
            return result
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}

    def close(self):
        """尽力关闭会话（服务端不支持时静默忽略）。"""
        if self.session_id:
            try:
                self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": "close"})
            except McdMcpError:
                pass
            self.session_id = None

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *exc):
        self.close()
