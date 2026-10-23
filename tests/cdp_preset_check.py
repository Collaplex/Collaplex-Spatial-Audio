"""点 EQ 预设, 验证前端与 DSP 都真的变了。"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

import websocket

API = "http://127.0.0.1:8789"
page = subprocess.Popen([
    "google-chrome", "--headless=new", "--disable-gpu", "--remote-debugging-port=9333",
    "--remote-allow-origins=*", "--user-data-dir=/tmp/cxchrome4", API],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(4)

tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/list", timeout=15))
ws = websocket.create_connection([t for t in tabs if t.get("type") == "page"][0]["webSocketDebuggerUrl"], timeout=25)
_mid = [0]


def js(expr: str) -> object:
    _mid[0] += 1
    ws.send(json.dumps({"id": _mid[0], "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _mid[0]:
            return ((msg.get("result") or {}).get("result") or {}).get("value")


print("预设按钮:", js("Array.from(document.querySelectorAll('#presets button')).map(b => b.textContent).join(' / ')"))

for name in ("低音", "柔化"):
    js("Array.from(document.querySelectorAll('#presets button')).find(b => b.textContent === '%s').click()" % name)
    time.sleep(0.7)
    vals = js("Array.from(document.querySelectorAll('#eq input')).map(i => +i.value)")
    srv = json.load(urllib.request.urlopen(API + "/api/state", timeout=15))
    print()
    print("点[%s] 前端前6段 = %s" % (name, [round(v, 1) for v in vals[:6]]))
    print("        前端后6段 = %s" % [round(v, 1) for v in vals[-6:]])
    print("        服务端前6 = %s  后6 = %s" % (
        [round(g, 1) for g in srv["gains"][:6]], [round(g, 1) for g in srv["gains"][-6:]]))
    print("        状态栏 =", js("document.getElementById('status').textContent"))

# 收尾: 回到平直
js("Array.from(document.querySelectorAll('#presets button')).find(b => b.textContent === '平直').click()")
time.sleep(0.5)
print()
print("已复位到平直:", js("Array.from(document.querySelectorAll('#eq input')).every(i => +i.value === 0)"))
ws.close()
page.terminate()
