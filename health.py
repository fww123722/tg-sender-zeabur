#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健康检查 HTTP 服务（Zeabur 端口探测）。"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import PORT, log


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def start_health_server():
    """后台启动健康检查服务，失败不影响主流程。"""
    def _run():
        try:
            srv = HTTPServer(("0.0.0.0", PORT), _Handler)
            srv.serve_forever()
        except Exception as e:
            log.warning(f"⚠️ 健康检查服务启动失败: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    log.info(f"🩺 健康检查服务已启动 :{PORT}")
