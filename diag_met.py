"""Разовая диагностика: почему бот не прислал звезду по монете за указанный час. Ничего не отправляет."""
import datetime as dt
import os
import time

import config as C
import scanner as S

INST = os.environ.get("DIAG_INST", "MET-USDT-SWAP")
TZ = S.TASHKENT
# окно свечей для таблицы (время открытия свечи, Ташкент)
WIN_FROM = dt.datetime(2026, 11, 4, 18, 0, tzinfo=TZ)
WIN_TO = dt.datetime(2026, 11, 5, 6, 0, tzinfo=TZ)
DETAIL = [dt.datetime(2026, 11, 4, h, 0, tzinfo=TZ) for h in (21, 22, 23)] + [dt.datetime(2026, 11, 5, 0, 0, tzinfo=TZ)]


def ms(d):
    return int(d.timestamp() * 1000)


def hhmm(ts):
    return dt.datetime.fromtimestamp(ts / 1000, TZ).strftime("%d.%m %H:%M")


def raw(bar, limit):
    data = S.okx_get("/api/v5/market/candles", {"instId": INST, "bar": bar, "limit": str(limit)})
    time.sleep(S.PAUSE)
    return data


def trace(cs, i):
    """Те же проверки, что в star_info, по порядку, с цифрами. Возвращает (список строк, причина отказа или None)."""
    out, why = [], None

    def step(name, ok, text):
        nonlocal why
        out.append(f"   {'ok ' if ok else 'НЕТ'} {name}: {text}")
        if not ok and why is None:
            why = name

    c = cs[i]
    rng = c["h"] - c["l"]
    top, bottom = max(c["o"], c["c"]), min(c["o"], c["c"])
    body, upper, lower = top - bottom, c["h"] - top, bottom - c["l"]
    step("свеча", True, f"O {S.price(c['o'])} H {S.price(c['h'])} L {S.price(c['l'])} C {S.price(c['c'])} "
                        f"({'красная' if c['c'] < c['o'] else 'зелёная'})")
    if rng <= 0:
        step("диапазон", False, "нулевой")
        return out, why
    u, b, lo = upper / rng * 100, body / rng * 100, lower / rng * 100
    ratio = upper / body if body > 0 else float("inf")
    pats = S.get_patterns()
    p = pats[0]
    step("форма: верхняя тень", u >= p["upper_min"] * 100, f"{u:.0f}% (нужно ≥ {p['upper_min'] * 100:.0f}%)")
    step("форма: тело", b <= p["body_max"] * 100, f"{b:.0f}% (нужно ≤ {p['body_max'] * 100:.0f}%)")
    step("форма: нижняя тень", p["lower_min"] * 100 <= lo <= p["lower_max"] * 100,
         f"{lo:.0f}% (нужно ≤ {p['lower_max'] * 100:.0f}%)")
    step("форма: тень ≥ 2 тел", upper >= body * p["upper_to_body"], f"верхняя тень = {ratio:.1f} тела (нужно ≥ {p['upper_to_body']:g})")
    lb = C.LOCAL_HIGH_LOOKBACK
    prev_high = max(x["h"] for x in cs[i - lb:i])
    step("локальный максимум (10 свечей)", c["h"] >= prev_high, f"H {S.price(c['h'])} против максимума прошлых 10: {S.price(prev_high)}")
    atr_v = S.atr_last(cs[:i + 1], 14)
    step("размер ≥ 0.5 ATR", atr_v is not None and rng >= atr_v * 0.5, f"диапазон = {rng / atr_v:.2f} ATR" if atr_v else "нет ATR")
    closes = [x["c"] for x in cs[:i + 1]]
    sma = S.sma_series(closes, 20)
    step("цена выше SMA20", closes[i] > sma[i], f"close {S.price(closes[i])} против SMA {S.price(sma[i])}")
    step("SMA20 растёт", sma[i] > sma[i - 1], f"{S.price(sma[i - 1])} -> {S.price(sma[i])}")
    n, j = 0, i
    while j >= 0 and sma[j] is not None and closes[j] > sma[j]:
        n += 1
        j -= 1
    step("≥3 свечи подряд выше SMA", n >= 3, f"{n}")
    rs = S.rsi_series(closes)
    step("RSI(14) ≥ 70 (был включён 04.11)", rs[i] is not None and rs[i] >= 70, f"RSI = {rs[i]:.1f}" if rs[i] is not None else "нет")
    return out, why


def verdict(closed, i, rsi_on):
    old = getattr(C, "RSI_FILTER", None)
    C.RSI_FILTER = rsi_on
    try:
        return S.star_info(closed, i)
    finally:
        C.RSI_FILTER = old


def main():
    print(f"Диагностика {INST}, конфиг сейчас: RSI_FILTER={C.RSI_FILTER}, RSI_MIN={C.RSI_MIN}, TOP_COINS={C.TOP_COINS}, "
          f"ONLY_RISING={C.ONLY_RISING}, MIN_TURNOVER_USDT={C.MIN_TURNOVER_USDT:,}")
    data = raw("1H", 300)
    if not data:
        print("свечи не получены")
        return
    rows = list(reversed(data))
    cs = [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
           "v": float(r[5]), "q": float(r[7]), "closed": r[8] == "1"} for r in rows]
    print(f"получено {len(cs)} свечей 1H: с {hhmm(cs[0]['ts'])} по {hhmm(cs[-1]['ts'])} (Ташкент)")
    idx = {c["ts"]: k for k, c in enumerate(cs)}

    # --- что видел бот в 19:02 UTC 04.11 (00:02 Ташкент 05.11)
    scan_ts = ms(dt.datetime(2026, 11, 4, 23, 0, tzinfo=TZ))
    k = idx.get(scan_ts)
    if k is not None:
        # 24 часа до момента проверки: оборот и изменение цены (приближённо по свечам 1ч)
        last_k = k + 1  # формирующаяся свеча 00:00 Ташкент; цена на момент проверки ~ её открытие
        if last_k < len(cs):
            last = cs[last_k]["o"]
            win = cs[last_k - 24:last_k]
            open24 = win[0]["o"]
            turn = sum(x["q"] for x in win)
            print(f"\nМонета на момент проверки (приближённо по свечам): цена {S.price(last)}, "
                  f"24ч назад {S.price(open24)} => {((last / open24) - 1) * 100:+.1f}% за сутки; оборот за 24ч ≈ {turn / 1e6:.1f} млн USDT "
                  f"(порог {C.MIN_TURNOVER_USDT / 1e6:g} млн)")

    # --- таблица по свечам
    print("\nСвечи MET 1H (время открытия, Ташкент): форма и вердикт бота")
    print("время        верхн  тело  нижн   RSI-ON(как было 04.11)  RSI-OFF(как сейчас)")
    for c in cs:
        if not (ms(WIN_FROM) <= c["ts"] <= ms(WIN_TO)) or not c["closed"]:
            continue
        k = idx[c["ts"]]
        closed_upto = [x for x in cs[:k + 1] if x["closed"]][-S.CANDLES:]
        i = len(closed_upto) - 1
        rng = c["h"] - c["l"]
        top, bottom = max(c["o"], c["c"]), min(c["o"], c["c"])
        u = (c["h"] - top) / rng * 100 if rng else 0
        b = (top - bottom) / rng * 100 if rng else 0
        lo = (bottom - c["l"]) / rng * 100 if rng else 0
        on = verdict(closed_upto, i, True)
        off = verdict(closed_upto, i, False)
        print(f"{hhmm(c['ts'])}   {u:4.0f}% {b:4.0f}% {lo:4.0f}%   {'ЗВЕЗДА' if on else '-':<22} {'ЗВЕЗДА' if off else '-'}")

    # --- подробно по нужным свечам
    for d in DETAIL:
        k = idx.get(ms(d))
        if k is None or not cs[k]["closed"]:
            continue
        closed_upto = [x for x in cs[:k + 1] if x["closed"]][-S.CANDLES:]
        i = len(closed_upto) - 1
        print(f"\n=== Свеча {hhmm(cs[k]['ts'])} (закрылась {hhmm(cs[k]['ts'] + 3_600_000)}), проверки по порядку ===")
        lines, why = trace(closed_upto, i)
        print("\n".join(lines))
        print(f"   -> первая причина отказа: {why or 'нет, все проверки пройдены'}; "
              f"star_info при RSI вкл: {bool(verdict(closed_upto, i, True))}, при RSI выкл: {bool(verdict(closed_upto, i, False))}")

    # --- текущее место в списке по обороту
    tick = S.okx_get("/api/v5/market/tickers", {"instType": "SWAP"})
    time.sleep(S.PAUSE)
    if tick:
        rs_ = []
        for d in tick:
            if d.get("instId", "").endswith("-USDT-SWAP"):
                try:
                    rs_.append((float(d["volCcy24h"]) * float(d["last"]), d["instId"]))
                except (KeyError, ValueError):
                    pass
        rs_.sort(reverse=True)
        names = [n for _, n in rs_]
        if INST in names:
            print(f"\nСейчас {INST}: {names.index(INST) + 1}-е место из {len(names)} по обороту, оборот {rs_[names.index(INST)][0] / 1e6:.1f} млн USDT")


if __name__ == "__main__":
    main()
