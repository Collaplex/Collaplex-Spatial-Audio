"""Collaplex 音效控制面板 —— 本地服务(纯标准库, 无第三方依赖)。

    python3 web/server.py [端口]        默认 8789

页面:      /
接口:
    GET  /api/state              参数 + DSP 实时电平
    POST /api/eq                 {"gains": [32 个 dB]}
    POST /api/volume             {"db": 数值}
    POST /api/loudness           {"target_db": 数值, "on": true/false}
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dsp"))

import common  # noqa: E402

LOUDNESS_JSON = os.path.expanduser("~/.local/state/cinema-spatial/loudness.json")
UI = os.path.join(HERE, "index.html")
DEFAULT_PORT = 8789


def _as_float(value: object, fallback: float = 0.0) -> float:
    """把 JSON 取到的值转 float(来源类型不安全, 这里只做值校验)。"""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback


def read_loudness() -> dict[str, object]:
    """读响度归一化设置(归一化 DSP 每秒重读这个文件)。"""
    try:
        with open(LOUDNESS_JSON, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return cast(dict[str, object], data)
    except (OSError, ValueError):
        pass
    return {"on": True, "target_db": -16.0}


def write_loudness(target_db: float, on: bool) -> None:
    os.makedirs(os.path.dirname(LOUDNESS_JSON), exist_ok=True)
    with open(LOUDNESS_JSON, "w", encoding="utf-8") as fh:
        json.dump({"on": bool(on), "target_db": float(target_db)}, fh)


class Handler(BaseHTTPRequestHandler):
    store = common.open_store()
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: object, code: int = 200) -> None:
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}
        return cast(dict[str, object], data) if isinstance(data, dict) else {}

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            try:
                with open(UI, "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            except OSError:
                self._send("面板文件缺失".encode("utf-8"), "text/plain; charset=utf-8", 500)
            return
        if self.path == "/api/state":
            gains, volume_db, _ver = common.read_params(self.store)
            loudness = read_loudness()
            self._send_json({
                "freqs": common.EQ_FREQS,
                "gains": gains,
                "volume_db": volume_db,
                "gain_limit": common.GAIN_LIMIT,
                "volume_limit": common.VOLUME_LIMIT,
                "loudness": {
                    "target_db": _as_float(loudness.get("target_db", -16.0), -16.0),
                    "on": bool(loudness.get("on", True)),
                },
                "levels": common.read_state(self.store),
            })
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        data = self._read_json()
        if self.path == "/api/eq":
            raw_gains = data.get("gains")
            if not isinstance(raw_gains, list) or len(raw_gains) != common.EQ_BANDS:
                self._send_json({"error": "gains 必须是 %d 个数" % common.EQ_BANDS}, 400)
                return
            items = cast(list[object], raw_gains)
            gains = [max(-common.GAIN_LIMIT, min(common.GAIN_LIMIT, _as_float(item))) for item in items]
            _g, volume_db, _v = common.read_params(self.store)
            version = common.write_params(self.store, gains, volume_db)
            self._send_json({"ok": True, "version": version})
            return
        if self.path == "/api/volume":
            volume_db = max(-common.VOLUME_LIMIT,
                            min(common.VOLUME_LIMIT, _as_float(data.get("db", 0.0))))
            gains, _old, _v = common.read_params(self.store)
            version = common.write_params(self.store, gains, volume_db)
            self._send_json({"ok": True, "version": version, "volume_db": volume_db})
            return
        if self.path == "/api/loudness":
            current = read_loudness()
            target_db = _as_float(data.get("target_db", current.get("target_db", -16.0)), -16.0)
            on = bool(data.get("on", current.get("on", True)))
            write_loudness(max(-40.0, min(-5.0, target_db)), on)
            self._send_json({"ok": True, "loudness": read_loudness()})
            return
        self._send_json({"error": "not found"}, 404)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Collaplex 音效控制面板: http://127.0.0.1:%d" % port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
