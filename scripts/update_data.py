"""
scripts/update_data.py
매일 7시 GitHub Actions에서 실행:
  1. Yahoo Finance에서 SOXL 최신 가격 fetch
  2. data/prices.json 업데이트
  3. 엔진으로 오늘 주문 계산 → data/today.json
"""

import json, os, math, datetime, sys, time
import urllib.request, urllib.error

# ── 경로 설정 ─────────────────────────────────────────────────────
ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_FILE = os.path.join(ROOT, "data", "prices.json")
TODAY_FILE  = os.path.join(ROOT, "data",  "today.json")

# ── Yahoo Finance fetch ───────────────────────────────────────────
def fetch_soxl(days=10):
    """최근 N일 SOXL OHLCV 가져오기"""
    end   = int(time.time())
    start = end - days * 86400 * 2  # 여유있게 2배

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/SOXL"
        f"?period1={start}&period2={end}&interval=1d&events=history"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    for attempt in range(3):
        try:
            req  = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            result = data["chart"]["result"][0]

            timestamps = result["timestamp"]
            ohlcv      = result["indicators"]["quote"][0]
            adjclose   = result["indicators"].get("adjclose", [{}])[0].get("adjclose", ohlcv["close"])

            records = []
            for i, ts in enumerate(timestamps):
                dt = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                o  = ohlcv["open"][i]
                h  = ohlcv["high"][i]
                l  = ohlcv["low"][i]
                c  = adjclose[i] if adjclose[i] else ohlcv["close"][i]
                v  = ohlcv["volume"][i]
                if all(x is not None for x in [o, h, l, c]):
                    records.append({
                        "date":   dt,
                        "open":   round(o, 4),
                        "high":   round(h, 4),
                        "low":    round(l, 4),
                        "close":  round(c, 4),
                        "volume": int(v) if v else 0,
                    })
            return records

        except Exception as e:
            print(f"  fetch 시도 {attempt+1} 실패: {e}")
            time.sleep(3)

    return []


# ── 가격 히스토리 업데이트 ─────────────────────────────────────────
def update_prices():
    # 기존 데이터 로드
    if os.path.exists(PRICES_FILE):
        with open(PRICES_FILE) as f:
            prices = json.load(f)
    else:
        prices = []

    existing_dates = {p["date"] for p in prices}

    # 최신 데이터 fetch
    new_records = fetch_soxl(days=15)
    added = 0
    for rec in new_records:
        if rec["date"] not in existing_dates:
            prices.append(rec)
            existing_dates.add(rec["date"])
            added += 1

    prices.sort(key=lambda x: x["date"])

    with open(PRICES_FILE, "w") as f:
        json.dump(prices, f, separators=(",", ":"))

    print(f"가격 데이터: 총 {len(prices)}일, 신규 {added}일 추가")
    return prices


# ── 무한매수법 엔진 ───────────────────────────────────────────────
# (GitHub Actions에서만 실행, 공개되지 않음)
def r2(v):
    return round(v * 100) / 100

def calc_qty(cs, price):
    if price <= 0:
        return 0
    return round(cs * 0.07 / price)

def buy1_factor(pct, hbq):
    if pct > 12: return 0.9950
    if pct > 5:  return 0.9897
    if pct > 1:  return 0.9936
    if pct > -1: return max(0.9950 - min(hbq * 0.00332, 0.025), 0.93)
    if pct > -3: return 0.9890
    if pct > -6: return 0.9807
    if pct > -10: return 0.9600
    return 0.9700

def sell1_price(pct, close, avg, hbq, carry):
    if carry:       return r2(close * 1.006)
    if pct > 10:    return r2(close * 1.0092)
    if pct > 5:     return r2(close * 1.0088)
    if pct > 1:     return r2(close * 1.0070)
    if avg > close:
        return r2(avg * 1.002) if hbq > 1.5 else r2(close * 1.005)
    ap = r2(avg * 1.002)
    if ap <= close and 1.5 < hbq < 2.1 and pct < 1.0:
        return ap
    return r2(close * 1.007)

def sell2_price(pct, close, avg, s1, carry, ps3):
    if carry:
        if pct < -2:               return r2(s1 * 1.002)
        if ps3 > 0 and ps3 > avg * 1.03: return ps3
        return r2(avg * 1.002)
    if s1 < close:  return r2(close * 1.007)
    if pct > 3:     return r2(close * 1.006)
    if pct < -1:    return r2(close * 1.003)
    return r2(avg * 1.002)

def recommend(cs, close, avg, holdings, ps2p=0, ps2q=0, ps3p=0, ps3q=0):
    buys, sells = [], []
    bq = calc_qty(cs, close)

    if holdings == 0:
        b1p = r2(close * 1.158);  b2p = r2(close * 0.997)
        buys = [(b1p, calc_qty(cs, b1p), "BUY1"), (b2p, calc_qty(cs, b2p), "BUY2")]
        return buys, sells

    pct = (close / avg - 1) * 100 if avg > 0 else 0
    hbq = holdings / bq if bq > 0 else 0

    b2p = r2(close * 0.997);   b2q = calc_qty(cs, b2p)
    b1p = r2(close * buy1_factor(pct, hbq)); b1q = calc_qty(cs, b1p)
    buys = [(b1p, b1q, "BUY1"), (b2p, b2q, "BUY2")] if b1p >= b2p \
      else [(b2p, b2q, "BUY2"), (b1p, b1q, "BUY1")]

    carry   = (ps2p > 0) and (pct < 5.0) and (holdings > 100)
    one_sell = (hbq < 1.5) or (pct > 7.0)

    sp1 = sell1_price(pct, close, avg, hbq, carry)
    sq1 = holdings if one_sell else (holdings if holdings <= bq else calc_qty(cs, sp1))
    sells.append((sp1, sq1, "SELL1"))

    if not one_sell:
        sp2 = sell2_price(pct, close, avg, sp1, carry, ps3p)
        sells.append((sp2, calc_qty(cs, b2p), "SELL2"))
        if carry:
            s3p = r2(ps2p + 0.01)
            if s3p >= sp1:
                sells.append((s3p, ps2q or sq1, "SELL3"))

    sells.sort(key=lambda x: x[0])
    for i, s in enumerate(sells):
        sells[i] = (s[0], s[1], f"SELL{i+1}")

    return buys, sells


# ── 오늘 추천 생성 ─────────────────────────────────────────────────
def generate_today(prices):
    if len(prices) < 2:
        print("가격 데이터 부족")
        return

    # 최신 종가
    latest = prices[-1]
    today_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")

    # data/today.json에서 이전 상태 로드
    state = {
        "cycle_start": 10000.0,
        "avg_price":   0.0,
        "holdings":    0,
        "prev_sell2_price": 0.0,
        "prev_sell2_qty":   0,
        "prev_sell3_price": 0.0,
        "prev_sell3_qty":   0,
        "history": []
    }
    if os.path.exists(TODAY_FILE):
        with open(TODAY_FILE) as f:
            saved = json.load(f)
            for k in ["cycle_start","avg_price","holdings",
                      "prev_sell2_price","prev_sell2_qty",
                      "prev_sell3_price","prev_sell3_qty","history"]:
                if k in saved:
                    state[k] = saved[k]

    cs   = state["cycle_start"]
    avg  = state["avg_price"]
    h    = state["holdings"]
    ps2p = state["prev_sell2_price"]
    ps2q = state["prev_sell2_qty"]
    ps3p = state["prev_sell3_price"]
    ps3q = state["prev_sell3_qty"]

    close = latest["close"]
    pct   = (close / avg - 1) * 100 if avg > 0 and h > 0 else 0

    buys, sells = recommend(cs, close, avg, h, ps2p, ps2q, ps3p, ps3q)

    # 등락률
    prev_close = prices[-2]["close"] if len(prices) >= 2 else close
    day_change = (close / prev_close - 1) * 100

    result = {
        "generated_at": datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))
        ).strftime("%Y-%m-%d %H:%M KST"),
        "date":        latest["date"],
        "close":       close,
        "day_change":  round(day_change, 2),
        "cycle_start": cs,
        "avg_price":   avg,
        "holdings":    h,
        "profit_pct":  round(pct, 2),
        "orders": {
            "buy":  [{"price": p, "qty": q, "label": l} for p,q,l in buys],
            "sell": [{"price": p, "qty": q, "label": l} for p,q,l in sells],
        },
        # 상태 carry-over (다음 실행을 위해 보존)
        "cycle_start":       cs,
        "avg_price":         avg,
        "holdings":          h,
        "prev_sell2_price":  ps2p,
        "prev_sell2_qty":    ps2q,
        "prev_sell3_price":  ps3p,
        "prev_sell3_qty":    ps3q,
        "history":           state.get("history", []),
    }

    with open(TODAY_FILE, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"오늘 추천 생성 완료: {latest['date']}, close=${close}")
    print(f"  매수: {[(p,q,l) for p,q,l in buys]}")
    print(f"  매도: {[(p,q,l) for p,q,l in sells]}")


# ── HTML 정적 데이터 업데이트 ─────────────────────────────────────
def update_html_prices(prices):
    """index.html 안의 _RAW= 정적 데이터를 최신 prices로 교체"""
    html_file = os.path.join(ROOT, "index.html")
    if not os.path.exists(html_file):
        print("index.html 없음, 스킵")
        return

    with open(html_file, encoding="utf-8") as f:
        html = f.read()

    # compact 형식: [date, open, high, low, close]
    compact = [[p["date"], p["open"], p["high"], p["low"], p["close"]] for p in prices]
    new_raw  = json.dumps(compact, separators=(",", ":"))

    # _RAW=[ ... ]; 패턴 교체
    import re
    pattern = r'const _RAW=\[.*?\];'
    replacement = f'const _RAW={new_raw};'

    new_html, n = re.subn(pattern, replacement, html, flags=re.DOTALL)
    if n == 0:
        print("index.html에서 _RAW 패턴을 찾지 못했습니다")
        return

    with open(html_file, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"index.html 정적 데이터 업데이트: {len(prices)}일")


# ── 메인 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== SOXL 데이터 업데이트 ===")
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    prices = update_prices()
    generate_today(prices)
    update_html_prices(prices)
    print("=== 완료 ===")
