"""panel_screenshot(CDP): 打开 http://127.0.0.1:8789/ 截图, 用于像素级验收。

用法: uv run --with websocket-client python panel_screenshot.py [输出png] [宽] [高]
前置: google-chrome --headless=new --disable-gpu --no-sandbox \
        --remote-debugging-port=9333 --remote-allow-origins='*' about:blank &
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request

import websocket

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/panel.png"
W = int(sys.argv[2]) if len(sys.argv) > 2 else 1200
H = int(sys.argv[3]) if len(sys.argv) > 3 else 1400
URL = "http://127.0.0.1:8789/"

targets = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/list"))
page = [t for t in targets if t["type"] == "page"][0]
ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
_seq = [0]


def cmd(method: str, **params: object) -> dict[str, object]:
    """发一条 CDP 命令并等回包。"""
    _seq[0] += 1
    ws.send(json.dumps({"id": _seq[0], "method": method, "params": params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _seq[0]:
            return msg


cmd("Page.enable")
cmd("Emulation.setDeviceMetricsOverride", width=W, height=H,
    deviceScaleFactor=1, mobile=False)
cmd("Page.navigate", url=URL)
time.sleep(3.0)          # 等页面 + SSE 首帧

# 顺手把新卡片里的文字读出来(和像素一起留证)
expr = ("JSON.stringify({sel:document.getElementById('outdev')?document.getElementById('outdev').options.length:-1,"
        "value:document.getElementById('outdev')?document.getElementById('outdev').value:'',"
        "state:document.getElementById('outstate')?document.getElementById('outstate').textContent:'',"
        "hint:document.getElementById('outhint')?document.getElementById('outhint').textContent:'',"
        "status:document.getElementById('status')?document.getElementById('status').textContent:''})")
got = cmd("Runtime.evaluate", expression=expr, returnByValue=True)
print("页面自述:", got.get("result", {}).get("result", {}).get("value"))

shot = cmd("Page.captureScreenshot", format="png", captureBeyondViewport=True)
data = base64.b64decode(str(shot["result"]["data"]))  # type: ignore[index]
with open(OUT, "wb") as fh:
    fh.write(data)
print("已保存:", OUT, len(data), "字节")
