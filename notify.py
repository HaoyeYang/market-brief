#!/usr/bin/env python3
"""Send a Market Brief completion notification without exposing secrets in argv."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import urllib.parse
import urllib.request


def send_telegram(status: str, message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": f"Market Brief — {status}\n{message}",
        "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"Telegram returned HTTP {response.status}")
    return True


def send_macos(status: str, message: str) -> bool:
    if platform.system() != "Darwin" or not os.path.exists("/usr/bin/osascript"):
        return False
    script = """on run argv
display notification (item 2 of argv) with title (item 1 of argv)
end run"""
    subprocess.run(
        ["/usr/bin/osascript", "-e", script, f"Market Brief — {status}", message],
        check=True,
        timeout=10,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    delivered = False
    try:
        delivered = send_telegram(args.status, args.message)
    except Exception as exc:
        print(f"Telegram notification failed: {exc}")
    if not delivered:
        try:
            delivered = send_macos(args.status, args.message)
        except Exception as exc:
            print(f"macOS notification failed: {exc}")
    if not delivered:
        print(f"Market Brief — {args.status}: {args.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
