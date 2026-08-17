"""Browser control via CDP. Read, edit, extend -- this file is yours."""
import base64, json, os, socket, time, urllib.request
from pathlib import Path
from urllib.parse import urlparse


def _load_env():
    p = Path(__file__).parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

NAME = os.environ.get("BU_NAME", "default")
SOCK = f"/tmp/bu-{NAME}.sock"
INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://", "chrome-extension://", "about:")


def _send(req):
    if hasattr(socket, "AF_UNIX") and os.path.exists(SOCK):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(60.0) # 60 second timeout to prevent hangs
        s.connect(SOCK)
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(60.0)
        s.connect(("127.0.0.1", 5008))
    s.sendall((json.dumps(req) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        try:
            chunk = s.recv(1 << 20)
            if not chunk: break
            data += chunk
        except socket.timeout:
            s.close()
            raise RuntimeError("Browser communication timed out after 60 seconds")
    s.close()
    r = json.loads(data)
    if "error" in r: raise RuntimeError(r["error"])
    return r


def cdp(method, session_id=None, **params):
    """Raw CDP. cdp('Page.navigate', url='...'), cdp('DOM.getDocument', depth=-1)."""
    return _send({"method": method, "params": params, "session_id": session_id}).get("result", {})


def drain_events():  return _send({"meta": "drain_events"})["events"]


# --- navigation / page ---
def goto_url(url):
    r = cdp("Page.navigate", url=url)
    d = (Path(__file__).parent / "domain-skills" / (urlparse(url).hostname or "").removeprefix("www.").split(".")[0])
    return {**r, "domain_skills": sorted(p.name for p in d.rglob("*.md"))[:10]} if d.is_dir() else r

def page_info():
    """{url, title, w, h, sx, sy, pw, ph} — viewport + scroll + page size."""
    dialog = _send({"meta": "pending_dialog"}).get("dialog")
    if dialog:
        return {"dialog": dialog}
    r = cdp("Runtime.evaluate",
            expression="JSON.stringify({url:location.href,title:document.title,w:innerWidth,h:innerHeight,sx:scrollX,sy:scrollY,pw:document.documentElement.scrollWidth,ph:document.documentElement.scrollHeight})",
            returnByValue=True)
    return json.loads(r["result"]["value"])

# --- input ---
_debug_click_counter = 0

def click_at_xy(x, y, button="left", clicks=1):
    if os.environ.get("BH_DEBUG_CLICKS"):
        global _debug_click_counter
        try:
            from PIL import Image, ImageDraw
            dpr = js("window.devicePixelRatio") or 1
            path = capture_screenshot(f"/tmp/debug_click_{_debug_click_counter}.png")
            img = Image.open(path)
            draw = ImageDraw.Draw(img)
            px, py = int(x * dpr), int(y * dpr)
            r = int(15 * dpr)
            draw.ellipse([px - r, py - r, px + r, py + r], outline="red", width=int(3 * dpr))
            draw.line([px - r - int(5 * dpr), py, px + r + int(5 * dpr), py], fill="red", width=int(2 * dpr))
            draw.line([px, py - r - int(5 * dpr), px, py + r + int(5 * dpr)], fill="red", width=int(2 * dpr))
            img.save(path)
            print(f"[debug_click] saved {path} (x={x}, y={y}, dpr={dpr})")
        except Exception as e:
            print(f"[debug_click] overlay failed: {e}")
        _debug_click_counter += 1
    cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button=button, clickCount=clicks)
    cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button=button, clickCount=clicks)

def type_text(text):
    cdp("Input.insertText", text=text)

_KEYS = {  # key → (windowsVirtualKeyCode, code, text)
    "Enter": (13, "Enter", "\r"), "Tab": (9, "Tab", "\t"), "Backspace": (8, "Backspace", ""),
    "Escape": (27, "Escape", ""), "Delete": (46, "Delete", ""), " ": (32, "Space", " "),
    "ArrowLeft": (37, "ArrowLeft", ""), "ArrowUp": (38, "ArrowUp", ""),
    "ArrowRight": (39, "ArrowRight", ""), "ArrowDown": (40, "ArrowDown", ""),
    "Home": (36, "Home", ""), "End": (35, "End", ""),
    "PageUp": (33, "PageUp", ""), "PageDown": (34, "PageDown", ""),
}
def press_key(key, modifiers=0):
    vk, code, text = _KEYS.get(key, (ord(key[0]) if len(key) == 1 else 0, key, key if len(key) == 1 else ""))
    base = {"key": key, "code": code, "modifiers": modifiers, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
    cdp("Input.dispatchKeyEvent", type="keyDown", **base, **({"text": text} if text else {}))
    if text and len(text) == 1:
        cdp("Input.dispatchKeyEvent", type="char", text=text, **{k: v for k, v in base.items() if k != "text"})
    cdp("Input.dispatchKeyEvent", type="keyUp", **base)

def scroll(x, y, dy=-300, dx=0):
    cdp("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=dx, deltaY=dy)


# --- visual ---
def capture_screenshot(path=None, full=False):
    """Captures a screenshot. If path is a boolean, treats it as 'full' and uses default path."""
    if isinstance(path, bool):
        full = path
        path = None
    path = path or f"/tmp/shot-{int(time.time())}.png"
    
    r = cdp("Page.captureScreenshot", format="png", captureBeyondViewport=full)
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["data"]))
    return path


# --- tabs ---
def list_tabs(include_chrome=True):
    out = []
    for t in cdp("Target.getTargets")["targetInfos"]:
        if t["type"] != "page": continue
        url = t.get("url", "")
        if not include_chrome and url.startswith(INTERNAL): continue
        out.append({"targetId": t["targetId"], "title": t.get("title", ""), "url": url, "activated": t.get("attached", False)})
    return out

def current_tab():
    t = cdp("Target.getTargetInfo").get("targetInfo", {})
    return {"targetId": t.get("targetId"), "url": t.get("url", ""), "title": t.get("title", "")}

def _mark_tab():
    pass # Removed to avoid focus stealing via title updates

def switch_tab(target_id, activate=False):
    if activate:
        cdp("Target.activateTarget", targetId=target_id)
    sid = cdp("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"]
    _send({"meta": "set_session", "session_id": sid})
    return sid

def find_tab_by_url(url_pattern):
    """Finds a tab whose URL contains the pattern."""
    for t in list_tabs():
        if url_pattern in t["url"]:
            return t["targetId"]
    return None

def new_tab(url="about:blank", activate=False, reuse=True):
    """Creates a new tab or reuses an existing one if reuse=True and URL matches."""
    if reuse and url != "about:blank":
        existing = find_tab_by_url(url.split('?')[0]) # Strip query params for matching
        if existing:
            switch_tab(existing, activate=activate)
            return existing

    # 1. Remember the currently active tab to prevent jumping
    tabs = list_tabs()
    original_active = next((t for t in tabs if t.get('activated')), None)
    if not original_active and tabs: original_active = tabs[0]
    
    # 2. Create the new tab
    tid = cdp("Target.createTarget", url="about:blank", newWindow=False)["targetId"]
    
    # 3. If we didn't want to activate it, immediately pull focus back to the original
    if not activate and original_active:
        cdp("Target.activateTarget", targetId=original_active['targetId'])
    
    switch_tab(tid, activate=activate)
    if url != "about:blank":
        goto_url(url)
    return tid

def close_tab(target_id):
    return cdp("Target.closeTarget", targetId=target_id)

def ensure_real_tab():
    tabs = list_tabs(include_chrome=False)
    if not tabs:
        return None
    try:
        cur = current_tab()
        if cur["url"] and not cur["url"].startswith(INTERNAL):
            return cur
    except Exception:
        pass
    switch_tab(tabs[0]["targetId"])
    return tabs[0]

def iframe_target(url_substr):
    for t in cdp("Target.getTargets")["targetInfos"]:
        if t["type"] == "iframe" and url_substr in t.get("url", ""):
            return t["targetId"]
    return None


# --- utility ---
def wait(seconds=1.0):
    time.sleep(seconds)

def wait_for_load(timeout=15.0):
    """Waits for document.readyState to be 'complete'."""
    if isinstance(timeout, str): # Handle cases where Gemini passes a selector by mistake
        return wait_for_selector(timeout)
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        if js("document.readyState") == "complete": return True
        time.sleep(0.3)
    return False

def wait_for_selector(selector, timeout=10.0):
    """Waits for an element to appear in the DOM."""
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        if js(f"document.querySelector({json.dumps(selector)})"): return True
        time.sleep(0.3)
    return False

def js(expression, target_id=None):
    sid = cdp("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"] if target_id else None
    if "return " in expression and not expression.strip().startswith("("):
        expression = f"(function(){{{expression}}})()"
    r = cdp("Runtime.evaluate", session_id=sid, expression=expression, returnByValue=True, awaitPromise=True)
    return r.get("result", {}).get("value")


# --- ALIASES FOR AGENT INTUITION ---
def goto(url): return goto_url(url)
def screenshot(path=None, full=False): return capture_screenshot(path, full)
def click(selector): 
    return js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(e){{e.scrollIntoView({{behavior:'smooth',block:'center'}});e.click();return true;}}return false;}})()")


def dispatch_key(selector, key="Enter", event="keypress"):
    kc = _KC.get(key, ord(key) if len(key) == 1 else 0)
    js(
        f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(e){{e.focus();e.dispatchEvent(new KeyboardEvent({json.dumps(event)},{{key:{json.dumps(key)},code:{json.dumps(key)},keyCode:{kc},which:{kc},bubbles:true}}));}}}})()"
    )

def upload_file(selector, path):
    doc = cdp("DOM.getDocument", depth=-1)
    nid = cdp("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=selector)["nodeId"]
    if not nid: raise RuntimeError(f"no element for {selector}")
    cdp("DOM.setFileInputFiles", files=[path] if isinstance(path, str) else list(path), nodeId=nid)

def http_get(url, headers=None, timeout=20.0):
    if os.environ.get("BROWSER_USE_API_KEY"):
        try:
            from fetch_use import fetch_sync
            return fetch_sync(url, headers=headers, timeout_ms=int(timeout * 1000)).text
        except ImportError:
            pass
    import gzip
    h = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
    if headers: h.update(headers)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip": data = gzip.decompress(data)
        return data.decode()
