"""Collaplex 音效控制面板 —— 本地服务(纯标准库, 无第三方依赖)。

    python3 web/server.py [端口]        默认 8789

页面:      /
接口:
    GET  /api/state     参数 + DSP 双声道电平
    POST /api/eq        {"gains": [32 个数]}   每段 dB(±15)
    POST /api/volume    {"db": -12..12}        总音量
    POST /api/wet       {"wet": 0..1}          混响湿量
    POST /api/loudness  {"target_db": -30..-6, "on": bool}   响度归一化(DSP 每秒重读该 JSON)
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "dsp"))

import common  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8789
UI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
LOUDNESS_JSON = os.path.expanduser("~/.local/state/cinema-spatial/loudness.json")


def _as_float(value: object, fallback: float = 0.0) -> float:
    """把 JSON 里取到的值转成 float(类型不安全, 只在值上做校验)。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def read_loudness() -> dict[str, object]:
    fallback: dict[str, object] = {"target_db": -16.0, "on": True}
    try:
        with open(LOUDNESS_JSON, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return fallback
    return cast(dict[str, object], data) if isinstance(data, dict) else fallback


def write_loudness(data: dict[str, object]) -> None:
    """原子写(临时文件 + rename), 归一化 DSP 每秒重读。"""
    directory = os.path.dirname(LOUDNESS_JSON)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = LOUDNESS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, LOUDNESS_JSON)


class Handler(BaseHTTPRequestHandler):
    store = common.open_store()
    lock = threading.Lock()

    def _send_json(self, payload: object, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
                with open(UI_PATH, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/state":
            with self.lock:
                gains, volume_db, wet, _version = common.read_params(self.store)
                levels = common.read_state(self.store)
            self._send_json({
                "freqs": common.EQ_FREQS,
                "loudness_levels": common.read_loudness_state(),
                "gains": gains,
                "volume_db": volume_db,
                "wet": wet,
                "gain_limit": common.GAIN_LIMIT,
                "volume_limit": common.VOLUME_LIMIT,
                "levels": levels,
                "loudness": read_loudness(),
            })
            return
        self.send_error(404)

    def do_POST(self) -> None:
        data = self._read_json()
        with self.lock:
            gains, volume_db, wet, _version = common.read_params(self.store)

            if self.path == "/api/eq":
                raw_gains = data.get("gains")
                if not isinstance(raw_gains, list) or len(raw_gains) != common.EQ_BANDS:
                    self._send_json(
                        {"ok": False, "error": "gains 需要 %d 个数" % common.EQ_BANDS}, 400)
                    return
                gains = [_clamp(_as_float(g), -common.GAIN_LIMIT, common.GAIN_LIMIT)
                         for g in cast(list[object], raw_gains)]
            elif self.path == "/api/volume":
                volume_db = _clamp(_as_float(data.get("db", 0.0)),
                                   -common.VOLUME_LIMIT, common.VOLUME_LIMIT)
            elif self.path == "/api/wet":
                wet = _clamp(_as_float(data.get("wet", 0.0)), 0.0, common.WET_LIMIT)
            elif self.path == "/api/loudness":
                current = read_loudness()
                target = _clamp(_as_float(data.get("target_db", current.get("target_db", -16.0)), -16.0),
                                -30.0, -6.0)
                on = data.get("on", current.get("on", True))
                write_loudness({"target_db": target, "on": bool(on)})
                self._send_json({"ok": True, "loudness": read_loudness()})
                return
            else:
                self.send_error(404)
                return

            common.write_params(self.store, gains, volume_db, wet)
        self._send_json({"ok": True, "gains": gains, "volume_db": volume_db, "wet": wet})

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("Collaplex 音效面板: http://127.0.0.1:%d" % PORT, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
