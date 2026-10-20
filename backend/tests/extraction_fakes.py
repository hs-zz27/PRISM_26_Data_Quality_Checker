"""Importable spawn targets. No model, HTTP, DB or parent monkeypatch dependency."""

import json
import os
from pathlib import Path
from time import monotonic, sleep

def fake_child(send, text, settings, rulebook_id):
    options = json.loads(text)
    marker = Path(options["marker"])
    marker.write_text(str(os.getpid()), encoding="utf-8")
    mode = options["mode"]
    try:
        if mode in {"hang", "partial"}:
            if mode == "partial":
                os.write(send.fileno(), b"\x00\x10")  # Deliberately incomplete frame header.
            until = monotonic() + 15
            while monotonic() < until:
                sleep(0.01)
            return
        if mode == "crash":
            os._exit(7)
        if mode == "eof":
            return
        if mode == "malformed":
            send.send_bytes(b'{"not_a_draft":true}')
            return
        if mode == "nonfinite":
            send.send_bytes(b'{"draft":{"value":NaN}}')
            return
        if mode == "error":
            send.send_bytes(b'{"error":"MODEL_UNAVAILABLE"}')
            return
        rules = []
        if mode == "large":
            rules = [{"rule_id": "R1", "description": "x" * (2 * 1024 * 1024),
                      "check_name": None, "parameters": {}, "origin": "extracted",
                      "source_sentence": text, "confirmation_state": "draft"}]
        book = {"rulebook_id": rulebook_id, "version": 1, "guideline_text": text,
                "rules": rules, "confirmation_state": "draft"}
        send.send_bytes(json.dumps({"draft": book}, allow_nan=False).encode())
    finally:
        send.close()
