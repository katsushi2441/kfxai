"""Bankr x402 自動支払いクライアント(kfreqai/kurage-hl/x402_pay.py と同型)。

Kurageの判断API(kcbrain/kfxbrain/ksbrain)は **Bankr x402の有料レール** で提供される。
このモジュールは設定されたウォレット鍵でEIP-3009(Base USDC)をサーバー側署名し、
呼び出しごとに自動で支払う。無料の直叩き経路はこのリポジトリには存在しない。

必要な環境変数:
  KURAGE_X402_WALLET_KEY  支払いに使うEVM秘密鍵(0x…)。Base USDCの残高が必要
  KURAGE_BANKR_BASE       (任意) Bankrのサービスベース。既定は公式エンドポイント
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.error
import urllib.request

from eth_account import Account
from eth_account.messages import encode_typed_data

BANKR_BASE = os.environ.get(
    "KURAGE_BANKR_BASE",
    "https://x402.bankr.bot/0x444fadbd6e1fed0cfbf7613b6c9f91b9021eecbd").rstrip("/")

_EIP712_DOMAIN = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]
_TRANSFER_TYPES = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]


def wallet_key() -> str:
    key = os.environ.get("KURAGE_X402_WALLET_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "KURAGE_X402_WALLET_KEY is required: Kurage brain APIs are paid via "
            "Bankr x402 (fund the wallet with Base USDC)")
    return key


def _post_json(url, payload, headers, timeout):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {"raw": body[:300]}
        return exc.code, data


def _sign_payment(challenge, private_key):
    accepts = challenge.get("accepts") or []
    acc = next((a for a in accepts if a.get("scheme") == "exact"), None)
    if not acc:
        raise RuntimeError("no 'exact' scheme in x402 challenge")
    account = Account.from_key(private_key)
    authorization = {
        "from": account.address,
        "to": acc["payTo"],
        "value": str(acc["maxAmountRequired"]),
        "validAfter": "0",
        "validBefore": str(int(time.time()) + int(acc.get("maxTimeoutSeconds") or 600)),
        "nonce": "0x" + secrets.token_hex(32),
    }
    extra = acc.get("extra") or {}
    full_message = {
        "types": {"EIP712Domain": _EIP712_DOMAIN,
                  "TransferWithAuthorization": _TRANSFER_TYPES},
        "domain": {"name": extra.get("name", "USD Coin"),
                   "version": extra.get("version", "2"),
                   "chainId": 8453,
                   "verifyingContract": acc["asset"]},
        "primaryType": "TransferWithAuthorization",
        "message": authorization,
    }
    signed = Account.sign_message(encode_typed_data(full_message=full_message), private_key)
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    payment = {
        "x402Version": challenge.get("x402Version", 1),
        "scheme": "exact",
        "network": acc.get("network"),
        "payload": {"signature": signature, "authorization": authorization},
    }
    return base64.b64encode(
        json.dumps(payment, separators=(",", ":")).encode("utf-8")).decode("ascii")


def pay_and_call(service: str, path: str, payload: dict, timeout: int = 300):
    """Bankrの有料エンドポイントを呼ぶ。402なら自動署名・自動支払いして再POST。

    service: "kcbrain" | "fxbrain" | "ksbrain" など Bankr上のサービス名
    path:    "/market/opportunity-ranking" などサービス配下のスキルパス
    """
    key = wallet_key()
    url = f"{BANKR_BASE}/{service}{path}"
    status, data = _post_json(url, payload, {}, min(timeout, 60))
    if status == 402:
        x_payment = _sign_payment(data, key)
        status, data = _post_json(url, payload, {"X-PAYMENT": x_payment}, timeout)
    if status == 402:
        raise RuntimeError(f"x402 payment rejected (insufficient USDC?): {str(data)[:160]}")
    if status != 200:
        raise RuntimeError(f"bankr {status}: {str(data)[:160]}")
    return data.get("response") if isinstance(data.get("response"), dict) else data
