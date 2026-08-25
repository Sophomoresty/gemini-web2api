#!/usr/bin/env python3
"""One-shot probe: is Gemini StreamGenerate reachable from this IP?

Prints 'blocked' (BardErrorInfo present) or 'UNBLOCKED' plus response size.
Exit code 1 while blocked - usable from cron/CI. No cookies, no config needed.
"""
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import uuid

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def probe() -> bool:
    ctx = ssl.create_default_context()
    html = urllib.request.urlopen(
        urllib.request.Request("https://gemini.google.com/app", headers={"User-Agent": UA}),
        context=ctx, timeout=15).read().decode("utf-8", errors="replace")
    bl = re.search(r'(boq_assistant-bard-web-server_\d+\.\d+_p\d+)', html)
    if not bl:
        print(f"{time.strftime('%H:%M:%S')} no BL in page - layout changed?")
        return False
    inner = [None] * 81
    inner[0] = ["hi", 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [1]; inner[7] = 1; inner[10] = 1; inner[11] = 0
    inner[17] = [[0]]; inner[18] = 0; inner[27] = 1; inner[30] = [4]
    inner[41] = [1]; inner[53] = 0; inner[59] = str(uuid.uuid4()).upper()
    inner[61] = []; inner[68] = 1; inner[79] = 1; inner[80] = 1
    body = urllib.parse.urlencode({"at": "", "f.req": json.dumps([None, json.dumps(inner)])}).encode()
    url = (f"https://gemini.google.com/_/BardChatUi/data/"
           f"assistant.lamda.BardFrontendService/StreamGenerate"
           f"?bl={bl.group(1)}&hl=en&_reqid={int(time.time()) % 1000000}&rt=c")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "X-Same-Domain": "1",
        "User-Agent": UA}, method="POST")
    resp = urllib.request.urlopen(req, context=ctx, timeout=45)
    raw = resp.read().decode("utf-8", errors="replace")
    ok = "BardErrorInfo" not in raw
    print(f"{time.strftime('%H:%M:%S')} {'UNBLOCKED' if ok else 'blocked'} ({len(raw)}b)")
    return ok


if __name__ == "__main__":
    sys.exit(0 if probe() else 1)
