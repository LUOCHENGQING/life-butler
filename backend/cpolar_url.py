"""从 cpolar 的本地管理接口取当前隧道公网地址，写到一个文件里方便查看。

cpolar 的 web 管理界面在 http://127.0.0.1:4042
但它的 /api/tunnels 有时返回空，所以这里做多路兜底：
1. 先试 /api/tunnels
2. 再试解析首页 HTML
3. 最后从最近的日志里 grep
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MGMT = "http://127.0.0.1:4042"
URL_RE = re.compile(r"https://([a-z0-9]+\.r\d+\.cpolar\.top)")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "公网地址.txt"


def try_api() -> str | None:
    for path in ("/api/tunnels", "/api/tunnels/"):
        try:
            with urllib.request.urlopen(MGMT + path, timeout=5) as r:
                raw = r.read().decode("utf-8", "ignore")
            if not raw.strip():
                continue
            data = json.loads(raw)
            tunnels = data.get("tunnels") or data.get("tunnels", [])
            if isinstance(tunnels, dict):
                tunnels = list(tunnels.values())
            for t in tunnels:
                u = t.get("public_url") or ""
                m = URL_RE.search(u)
                if m:
                    return m.group(0)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            continue
    return None


def try_html() -> str | None:
    try:
        with urllib.request.urlopen(MGMT + "/", timeout=5) as r:
            html = r.read().decode("utf-8", "ignore")
        m = URL_RE.search(html)
        return m.group(0) if m else None
    except (urllib.error.URLError, TimeoutError):
        return None


def try_logs() -> str | None:
    dirs = [
        Path.home() / ".cpolar" / "logs",
        Path.home() / "AppData" / "Local" / "cpolar" / "logs",
        Path("C:/ProgramData/cpolar/logs"),
    ]
    for d in dirs:
        if not d.exists():
            continue
        files = sorted(d.rglob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0)
        for f in reversed(files[-8:]):
            if not f.is_file():
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            m = URL_RE.search(txt)
            if m:
                return m.group(0)
    return None


def main() -> int:
    url = None
    for attempt in range(12):
        url = try_api() or try_html() or try_logs()
        if url:
            break
        time.sleep(2)

    if url:
        print("PUBLIC_URL=" + url)
        lines = [
            "小管家 · 公网访问地址",
            "=" * 46,
            "",
            url,
            "",
            "说明：",
            "  · 手机在外面用这个地址（电脑需保持开机）",
            "  · 手机浏览器菜单 →「添加到主屏幕」",
            "",
            "注意：cpolar 免费版每次重启地址会变，",
            "      以本次显示的为准。",
        ]
        OUT.write_text("\n".join(lines), encoding="utf-8")
        print("saved -> " + str(OUT))
        return 0

    print("FAIL: 未取到公网地址")
    print("提示：到 http://127.0.0.1:4042 手动查看")
    return 1


if __name__ == "__main__":
    sys.exit(main())
