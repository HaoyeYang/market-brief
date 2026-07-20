#!/usr/bin/env python3
"""Send a Market Brief completion notification without exposing secrets in argv."""

from __future__ import annotations

import argparse
import os
import platform
import smtplib
import ssl
import subprocess
import urllib.parse
import urllib.request
from email.message import EmailMessage


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


def build_email(status: str, message: str, sender: str, recipient: str, web_url: str) -> EmailMessage:
    email = EmailMessage()
    email["Subject"] = f"Market Brief · {status}"
    email["From"] = sender
    email["To"] = recipient
    lines = [
        f"Market Brief 状态：{status}",
        "",
        message,
    ]
    if web_url:
        lines.extend([
            "",
            "打开私人报告：",
            web_url,
            "",
            "该页面由 Google IAP 保护，请使用已授权的 Google 账号登录。",
        ])
    lines.extend(["", "这是一封自动通知邮件；报告仅供研究，不构成投资建议。"])
    email.set_content("\n".join(lines))
    return email


def send_gmail(status: str, message: str) -> bool:
    user = os.environ.get("GMAIL_SMTP_USER")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    recipient = os.environ.get("MARKET_BRIEF_EMAIL_TO")
    if not user or not app_password or not recipient:
        return False
    email = build_email(
        status, message, user, recipient,
        os.environ.get("MARKET_BRIEF_WEB_URL", ""),
    )
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15, context=context) as smtp:
        smtp.login(user, app_password)
        smtp.send_message(email)
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

    deliveries: list[str] = []
    try:
        if send_gmail(args.status, args.message):
            deliveries.append("gmail")
    except Exception as exc:
        print(f"Gmail notification failed: {exc}")
    try:
        if send_telegram(args.status, args.message):
            deliveries.append("telegram")
    except Exception as exc:
        print(f"Telegram notification failed: {exc}")
    if not deliveries:
        try:
            if send_macos(args.status, args.message):
                deliveries.append("macos")
        except Exception as exc:
            print(f"macOS notification failed: {exc}")
    if not deliveries:
        print(f"Market Brief — {args.status}: {args.message}")
    else:
        print(f"Market Brief notification delivered via {','.join(deliveries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
