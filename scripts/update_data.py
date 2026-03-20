"""
scripts/update_data.py
매일 GitHub Actions에서 실행:
  - 미국 장 마감(4pm ET = 21:00 UTC) 후 30분 → 21:30 UTC (평일 월~금)
  1. Yahoo Finance에서 SOXL 최신 가격만 fetch (최근 10일, 새 데이터만 append)
  2. data/prices.json 업데이트 (전체 정적 데이터 유지)
  3. 엔진으로 오늘 주문 계산 → data/today.json
"""

import json, os, math, datetime, sys, time
import urllib.request, urllib.error

# ── 경로 설정 ─────────────────────────────────────────────────────
ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_FILE = os.path.join(ROOT, "data", "prices.json")
TODAY_FILE  = os.path.join(ROOT, "data", "today.json")

# ── Yahoo Finance fetch (최근 N일만) ──────────────────────────────
def fetch_recent_soxl(days=10):
    end   = int(time.time()) + 86400
    start = end - (days + 5) * 86400

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
            resp = urllib.request.urlopen(req, timeout=20)
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

            print(f"  Yahoo fetch 성공: {len(records)}일 데이터")
            return records

        except Exception as e:
            print(f"  fetch 시도 {attempt+1} 실패: {e}")
            if attempt < 2:
                time.sleep(5)

    print("  경고: 모든 fetch 시도 실패")
    return []


# ── 가격 히스토리 업데이트 ─────────────────────────────────────────
def update_prices():
    prices = []
    if os.path.exists(PRICES_FILE):
        with open(PRICES_FILE) as f:
            try:
                prices = json.load(f)
                print(f"기존 데이터 로드: {len(prices)}일")
            except json.JSONDecodeError:
                print("  경고: prices.json 파싱 실패, 빈 데이터로 시작")
                prices = []
    else:
        print("prices.json 없음, 새로 생성")

    existing_dates = {p["date"] for p in prices}
    new_records = fetch_recent_soxl(days=10)
    added = 0
    skipped = 0

    today_kst = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).strftime("%Y-%m-%d")

    for rec in new_records:
        if rec["date"] not in existing_dates:
            if rec["date"] > today_kst:
                print(f"  미래 날짜 스킵: {rec['date']}")
                continue
            prices.append(rec)
            existing_dates.add(rec["date"])
            added += 1
        else:
            skipped += 1

    prices.sort(key=lambda x: x["date"])

    with open(PRICES_FILE, "w") as f:
        json.dump(prices, f, separators=(",", ":"))

    print(f"가격 데이터: 총 {len(prices)}일 | 신규 {added}일 추가 | {skipped}일 중복 스킵")
    return prices


# ── 무한매수법 엔진 ───────────────────────────────────────────────
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
        print("가격 데이터 부족 (최소 2일 필요)")
        return

    latest = prices[-1]

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
            try:
                saved = json.load(f)
                for k in ["cycle_start", "avg_price", "holdings",
                          "prev_sell2_price", "prev_sell2_qty",
                          "prev_sell3_price", "prev_sell3_qty", "history"]:
                    if k in saved:
                        state[k] = saved[k]
            except json.JSONDecodeError:
                print("  경고: today.json 파싱 실패, 초기값 사용")

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

    prev_close = prices[-2]["close"] if len(prices) >= 2 else close
    day_change = round((close / prev_close - 1) * 100, 2)

    now_kst = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).strftime("%Y-%m-%d %H:%M KST")

    result = {
        "generated_at":      now_kst,
        "date":              latest["date"],
        "close":             close,
        "day_change":        day_change,
        "cycle_start":       cs,
        "avg_price":         avg,
        "holdings":          h,
        "profit_pct":        round(pct, 2),
        "prev_sell2_price":  ps2p,
        "prev_sell2_qty":    ps2q,
        "prev_sell3_price":  ps3p,
        "prev_sell3_qty":    ps3q,
        "history":           state.get("history", []),
        "orders": {
            "buy":  [{"price": p, "qty": q, "label": l} for p, q, l in buys],
            "sell": [{"price": p, "qty": q, "label": l} for p, q, l in sells],
        },
    }

    with open(TODAY_FILE, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"오늘 추천 생성 완료: {latest['date']}, close=${close}")
    print(f"  등락: {day_change:+.2f}%")
    print(f"  매수: {[(p, q, l) for p, q, l in buys]}")
    print(f"  매도: {[(p, q, l) for p, q, l in sells]}")


# ── 메인 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("SOXL 데이터 업데이트")
    print(f"실행 시각 (UTC): {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

    prices = update_prices()

    if prices:
        generate_today(prices)
    else:
        print("오류: 가격 데이터 없음, today.json 생성 건너뜀")
        sys.exit(1)

    print("=" * 50)
    print("완료!")
    print("=" * 50)
