"""
Sends the daily text via Twilio.

Requires environment variables (set as GitHub Actions secrets):
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER,
  MY_PHONE_NUMBER, SITE_URL
"""

import os
import sys
from twilio.rest import Client


def main():
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    from_num = os.environ["TWILIO_FROM_NUMBER"]
    to_num = os.environ["MY_PHONE_NUMBER"]
    site = os.environ["SITE_URL"]

    body = f"Hey Brandon, here is your daily Phillies update. {site}"

    client = Client(sid, token)
    msg = client.messages.create(body=body, from_=from_num, to=to_num)
    print(f"Sent SMS {msg.sid} to {to_num}")


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        print(f"Missing environment variable: {e}", file=sys.stderr)
        sys.exit(1)
