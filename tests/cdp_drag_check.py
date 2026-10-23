"""用 CDP 发真实鼠标拖动, 验证总音量滑块不再被轮询弹回。

修前: 拖动中每 250ms 被 /api/state 覆盖 -> 松手后跳回旧值
修后: busy 标志挡住覆盖 -> 停在拖到的位置并生效
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

import websocket

API = "http://127.0.0.1:8789"

page = subprocess.Popen([
    "google-chrome", "--headless=new", "--disable-gpu",
    "--remote-debugging-port=9333", "--remote-allow-origins=*",
    "--user-data-dir=/tmp/cxchrome3", API],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(4)

tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/list", timeout=15))
ws_url = [t for t in tabs if t.get("type") == "page"][0]["webSocketDebuggerUrl"]
ws = websocket.create_connection(ws_url, timeout=25)
_mid = [0]


def cmd(method: str, **params: object) -> dict:
    _mid[0] += 1
    ws.send(json.dumps({"id": _mid[0], "method": method, "params": params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _mid[0]:
            return msg.get("result") or {}


def js(expr: str) -> object:
    res = cmd("Runtime.evaluate", expression=expr, returnByValue=True)
    return (res.get("result") or {}).get("value")


box = js("(() => { const r = document.getElementById('vol').getBoundingClientRect();"
         " return [r.left, r.top + r.height/2, r.width, r.height]; })()")
before = js("document.getElementById('vol').value")
print("滑块 box =", box, " 拖动前 vol =", before)

x1 = box[0] + box[2] * 0.5
x2 = box[0] + box[2] * 0.92
y = box[1]
cmd("Input.dispatchMouseEvent", type="mousePressed", x=x1, y=y, button="left", clickCount=1)
for i in range(1, 11):
    cmd("Input.dispatchMouseEvent", type="mouseMoved",
        x=x1 + (x2 - x1) * i / 10.0, y=y, button="left")
    time.sleep(0.09)
dragging = js("document.getElementById('vol').value")
cmd("Input.dispatchMouseEvent", type="mouseReleased", x=x2, y=y, button="left", clickCount=1)
time.sleep(0.15)
just_up = js("document.getElementById('vol').value")
print("拖动中 vol =", dragging, " 刚松手 vol =", just_up)

time.sleep(1.5)
after = js("document.getElementById('vol').value")
srv = json.load(urllib.request.urlopen(API + "/api/state", timeout=15))
print("1.5 秒后 vol =", after, " 服务端 volume_db =", srv["volume_db"])

same = abs(float(after) - float(just_up)) < 0.01
synced = abs(float(after) - float(srv["volume_db"])) < 0.51
print("结果:", "滑块停住且已生效 OK" if (same and synced) else "仍有回弹/未同步 FAIL")
ws.close()
page.terminate()
