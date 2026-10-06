"""
Смоук-тест живого API ikus.pesc.ru, по умолчанию только чтение.

  .venv/bin/python scripts/smoke.py           проверка
  .venv/bin/python scripts/smoke.py --login   вход со вторым фактором (отправит SMS)
  .venv/bin/python scripts/smoke.py --update  + передача показаний +1 (изменяет данные)

Учётные данные и токены: ~/.config/ha-pesc/smoke.json
  {"username": "+7...", "password": "...", "login_type": "phone", "auth": {...}}
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.pesc import pesc_api, pesc_client  # noqa: E402

CONFIG = Path.home() / ".config" / "ha-pesc" / "smoke.json"
FIXTURES = ROOT / "tests" / "fixtures"


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def save_config(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    os.chmod(CONFIG, 0o600)


def keys(obj, path="") -> set[str]:
    if isinstance(obj, dict):
        res = set()
        for key, val in obj.items():
            res.add(f"{path}.{key}")
            res |= keys(val, f"{path}.{key}")
        return res
    if isinstance(obj, list) and obj:
        return keys(obj[0], f"{path}[]")
    return set()


def compare(name: str, live) -> bool:
    fixture = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    missing = keys(fixture) - keys(live)
    added = keys(live) - keys(fixture)
    status = "FAIL" if missing else "ok"
    print(f"  {status:4} {name}")
    for key in sorted(missing):
        print(f"       - {key}")
    for key in sorted(added):
        print(f"       + {key}")
    return not missing


async def login(api: pesc_api.PescApi, cfg: dict) -> None:
    transaction = await api.async_login(
        cfg["username"], cfg["password"], cfg["login_type"]
    )
    print(f"способы подтверждения: {transaction['types']}")
    transaction = await api.async_login_confirmation_send(
        transaction, pesc_client.CONFIRMATION_SMS
    )
    code = input("код из SMS: ").strip()
    cfg["auth"] = await api.async_login_confirmation_verify(transaction, code)
    save_config(cfg)
    print("вход выполнен, токены сохранены")


async def relogin(api: pesc_api.PescApi, cfg: dict) -> None:
    auth = await api.async_relogin(
        cfg["username"], cfg["password"], cfg["auth"], cfg["login_type"]
    )
    cfg["auth"] = cfg["auth"] | auth
    save_config(cfg)
    print("  ok   relogin по verified")


async def check(api: pesc_api.PescApi, cfg: dict) -> bool:
    client = api.client
    try:
        await client.async_profile()
    except pesc_client.ClientAuthError:
        await relogin(api, cfg)

    ok = compare("profile", await client.async_profile())
    accounts = await client.async_accounts()
    ok &= compare("accounts", accounts)
    for account in accounts:
        acc_id = account["id"]
        print(f"счёт провайдера {account['service']['providerId']}:")
        reading_type = await client.async_reading_type(acc_id)
        print(
            f"  {'ok' if reading_type in ('manual', 'auto') else 'FAIL':4} "
            f"reading-types: {reading_type}"
        )
        ok &= compare("address", await client.async_address(acc_id))
        ok &= compare("meters", await client.async_meters(acc_id))
        ok &= compare("details", await client.async_details(acc_id))

    print("полный цикл интеграции:")
    await api.async_fetch_all()
    for ind in api.meters:
        subservice = api.subservice(ind.meter.subservice_id)
        tariff = api.tariff(ind)
        rate = tariff.rate(ind) if tariff else None
        print(
            f"  {ind.name}: value={type(ind.value).__name__} date={ind.date} "
            f"utility={subservice['utility'] if subservice else None} "
            f"rate={rate.value if rate else None}"
        )
        ok &= subservice is not None
    return ok


async def update(api: pesc_api.PescApi) -> bool:
    meter = api.meters[0]
    inds = [ind for ind in api.meters if ind.meter.id == meter.meter.id]
    values = [
        pesc_client.UpdateValuePayload(scaleId=ind.scale_id, value=int(ind.value) + 1)
        for ind in inds
    ]
    print(f"передача показаний (+1): {[(ind.name, ind.value) for ind in inds]}")
    await api.async_update_value(meter, values)

    await api.async_fetch_all()
    expected = {val["scaleId"]: val["value"] for val in values}
    ok = True
    for ind in api.meters:
        if ind.meter.id == meter.meter.id:
            match = ind.value == expected[ind.scale_id]
            ok &= match
            print(
                f"  {'ok' if match else 'FAIL':4} {ind.name}: {ind.value} "
                f"(ожидалось {expected[ind.scale_id]}) date={ind.date}"
            )
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true")
    parser.add_argument(
        "--update", action="store_true", help="передать показания +1 (изменяет данные)"
    )
    args = parser.parse_args()

    cfg = load_config()
    async with aiohttp.ClientSession() as session:
        api = pesc_api.PescApi(pesc_client.PescClient(session, cfg.get("auth")))
        if args.login:
            await login(api, cfg)
            return 0
        try:
            ok = await check(api, cfg)
            if args.update:
                ok &= await update(api)
        except pesc_client.ClientTwoFactorRequired:
            print("verified не принят, нужен --login")
            return 2
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
