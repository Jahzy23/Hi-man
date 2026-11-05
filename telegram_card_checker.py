# telegram_card_checker.py — Python 3
# Telegram bot: online card check + AVS via Stripe
# Usage:
#   1. Create bot with @BotFather, get token
#   2. Set BOT_TOKEN and STRIPE_SK below (or env vars)
#   3. pip install requests stripe
#   4. python telegram_card_checker.py
# Commands: /start  /check cc|mm|yy|cvv|zip|address
# Or just send: 4111111111111111|12|28|123|10001|123 Main St

import os
import re
import time
import requests
import stripe

BOT_TOKEN = os.environ.get("BOT_TOKEN", "9177827056:AAFn_21ShAxm5ZS1TjUIlDcBPLXi8CO66rI")
STRIPE_SK = os.environ.get("STRIPE_SK", "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")

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OFFSET = 0

def send(chat_id, text):
    requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

def check_card(pan, mm, yy, cvv, zipc, addr, name="Card Holder"):
    stripe.api_key = STRIPE_SK
    try:
        pm = stripe.PaymentMethod.create(
            type="card",
            card={"number": pan, "exp_month": int(mm), "exp_year": int(yy), "cvc": cvv},
            billing_details={
                "name": name,
                "address": {"line1": addr or None, "postal_code": zipc or None, "country": "US"}
            }
        )
        pi = stripe.PaymentIntent.create(
            amount=50,
            currency="usd",
            payment_method=pm.id,
            confirm=True,
            capture_method="manual",
            off_session=True,
            description="card check"
        )
        status = pi.status
        avs_zip = avs_addr = cvc = decline = None
        if pi.charges and pi.charges.data:
            ch = pi.charges.data[0]
            if ch.payment_method_details and ch.payment_method_details.card:
                checks = ch.payment_method_details.card.checks or {}
                avs_zip = checks.get("address_postal_code_check")
                avs_addr = checks.get("address_line1_check")
                cvc = checks.get("cvc_check")
            if ch.outcome:
                decline = ch.outcome.reason
        if status in ("requires_capture", "succeeded"):
            try:
                stripe.PaymentIntent.cancel(pi.id)
            except Exception:
                pass
        live = status in ("requires_capture", "succeeded", "processing")
        return {
            "status": "LIVE" if live else "DEAD",
            "pi_status": status,
            "avs_zip": avs_zip,
            "avs_address": avs_addr,
            "cvc_check": cvc,
            "decline": decline
        }
    except stripe.error.CardError as e:
        err = e.json_body.get("error", {}) if hasattr(e, "json_body") else {}
        return {
            "status": "DEAD",
            "reason": err.get("message", str(e)),
            "decline_code": err.get("decline_code")
        }
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}

def parse_card(text):
    parts = re.split(r"[|/\s]+", text.strip())
    if len(parts) < 4:
        return None
    pan = re.sub(r"\D", "", parts[0])
    mm = parts[1].zfill(2)
    yy = parts[2]
    if len(yy) == 2:
        yy = "20" + yy
    cvv = parts[3]
    zipc = parts[4] if len(parts) > 4 else ""
    addr = " ".join(parts[5:]) if len(parts) > 5 else ""
    if len(pan) < 13:
        return None
    return pan, mm, yy, cvv, zipc, addr

def handle(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    if not text:
        return
    if text.startswith("/start"):
        send(chat_id, "Send card as:\n<code>cc|mm|yy|cvv|zip|address</code>\n\nExample:\n<code>4111111111111111|12|28|123|10001|123 Main St</code>")
        return
    if text.startswith("/check "):
        text = text[7:].strip()
    parsed = parse_card(text)
    if not parsed:
        send(chat_id, "Bad format. Use: cc|mm|yy|cvv|zip|address")
        return
    pan, mm, yy, cvv, zipc, addr = parsed
    send(chat_id, "Checking...")
    res = check_card(pan, mm, yy, cvv, zipc, addr)
    if res["status"] == "LIVE":
        reply = (
            f"<b>LIVE</b>\n"
            f"Status: {res['pi_status']}\n"
            f"AVS ZIP: {res.get('avs_zip') or 'n/a'}\n"
            f"AVS Address: {res.get('avs_address') or 'n/a'}\n"
            f"CVC: {res.get('cvc_check') or 'n/a'}"
        )
    elif res["status"] == "DEAD":
        reply = (
            f"<b>DEAD</b>\n"
            f"{res.get('reason', res.get('pi_status', 'declined'))}\n"
            f"Code: {res.get('decline_code', res.get('decline', ''))}"
        )
    else:
        reply = f"ERROR\n{res.get('reason', 'unknown')}"
    send(chat_id, reply)

def main():
    global OFFSET
    print("Bot running...")
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": OFFSET, "timeout": 30}, timeout=35)
            data = r.json()
            for upd in data.get("result", []):
                OFFSET = upd["update_id"] + 1
                handle(upd)
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)

if __name__ == "__main__":
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Set BOT_TOKEN and STRIPE_SK (env or in file)")
    else:
        main()
