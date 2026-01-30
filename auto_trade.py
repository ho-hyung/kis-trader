"""
자동 매매 스크립트 (멀티 전략)
GitHub Actions에서 정기 실행

종목별 전략:
- VRT (상승 추세): 눌림목 매수 (현재가 < SMA), 익절 +10%, 손절 -5%
- ORCL (하락 추세): 반등 매수 (현재가 > SMA), 익절 +7%, 손절 -4%
"""

import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

# 매매 기록 파일 경로
TRADE_HISTORY_FILE = "trade_history.json"

# ========================================
# 설정
# ========================================
# 매매 대상 종목 리스트 (종목별 전략 설정)
TARGETS = [
    {
        "symbol": "VRT",
        "exchange": "NYS",
        "strategy": "pullback",      # 눌림목 매수 (상승 추세용)
        "take_profit": 10.0,         # +10% 익절
        "stop_loss": -5.0,           # -5% 손절
        "use_sma60": True,           # 60일 SMA 체크 (장기 추세 확인)
    },
    {
        "symbol": "ORCL",
        "exchange": "NYS",
        "strategy": "breakout",      # 반등 매수 (하락 추세용)
        "take_profit": 7.0,          # +7% 익절 (보수적)
        "stop_loss": -4.0,           # -4% 손절 (빠른 손절)
        "max_rsi": 70,               # RSI 70 이상이면 매수 안 함 (과매수 회피)
    },
]

IS_REAL_TRADING = True  # 실제 주문 활성화

# ========================================
# 환경변수 로드 (로컬 or GitHub Actions)
# ========================================
load_dotenv()


def get_env(key: str, default: str = None) -> str:
    """환경변수 가져오기"""
    return os.getenv(key, default)


# ========================================
# KIS API 클래스
# ========================================
class KisAuth:
    BASE_URL = "https://openapi.koreainvestment.com:9443"

    def __init__(self):
        self.app_key = get_env("KIS_APP_KEY")
        self.app_secret = get_env("KIS_APP_SECRET")
        self.account_number = get_env("KIS_ACCOUNT_NUMBER")
        self.account_product_code = get_env("KIS_ACCOUNT_PRODUCT_CODE", "01")
        self.access_token = None

        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY and KIS_APP_SECRET are required")

    def get_access_token(self) -> str:
        url = f"{self.BASE_URL}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        response = requests.post(url, json=body, timeout=10)
        response.raise_for_status()

        data = response.json()
        self.access_token = data.get("access_token")
        if not self.access_token:
            raise ValueError(f"Token error: {data}")

        return self.access_token

    def get_auth_headers(self, tr_id: str) -> dict:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }


class KisOverseas:
    def __init__(self, auth: KisAuth):
        self.auth = auth
        self.base_url = auth.BASE_URL

    def get_current_price(self, symbol: str, exchange: str = "NYS") -> dict:
        """현재가 조회"""
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        headers = self.auth.get_auth_headers("HHDFS00000300")
        params = {"AUTH": "", "EXCD": exchange, "SYMB": symbol}

        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"API error: {data.get('msg1')}")

        output = data.get("output", {})
        return {
            "price": float(output.get("last", 0) or 0),
            "change_rate": float(output.get("rate", 0) or 0),
        }

    def get_daily_prices(self, symbol: str, exchange: str = "NYS", days: int = 20) -> list:
        """일별 시세 조회 (이동평균 계산용)"""
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/dailyprice"
        headers = self.auth.get_auth_headers("HHDFS76240000")

        # 거래소 코드 매핑
        excd_map = {"NYS": "NYS", "NAS": "NAS", "AMS": "AMS"}

        params = {
            "AUTH": "",
            "EXCD": excd_map.get(exchange, "NYS"),
            "SYMB": symbol,
            "GUBN": "0",  # 0: 일, 1: 주, 2: 월
            "BYMD": "",
            "MODP": "1",
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"API error: {data.get('msg1')}")

        prices = []
        for item in data.get("output2", [])[:days]:
            close = item.get("clos")
            if close:
                prices.append(float(close))

        return prices

    def buy_market_order(self, symbol: str, quantity: int, exchange: str = "NYS") -> dict:
        """시장가 매수"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        tr_id = "TTTT1002U"  # 실전투자 해외매수

        exchange_map = {"NYS": "NYSE", "NAS": "NASD", "AMS": "AMEX"}
        headers = self.auth.get_auth_headers(tr_id)

        body = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": exchange_map.get(exchange, "NYSE"),
            "PDNO": symbol,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": "0",  # 시장가
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",
        }

        if not IS_REAL_TRADING:
            return {
                "success": True,
                "mode": "simulation",
                "order_no": "VIRTUAL",
            }

        response = requests.post(url, headers=headers, json=body, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"Order failed: {data.get('msg1')}")

        return {
            "success": True,
            "mode": "real",
            "order_no": data.get("output", {}).get("ODNO"),
        }

    def get_holdings(self) -> list:
        """해외주식 보유 잔고 조회"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = self.auth.get_auth_headers("TTTS3012R")
        params = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        holdings = []
        for item in data.get("output1", []):
            qty = int(item.get("ovrs_cblc_qty", 0) or 0)
            if qty > 0:
                holdings.append({
                    "symbol": item.get("ovrs_pdno"),
                    "quantity": qty,
                    "avg_price": float(item.get("pchs_avg_pric", 0) or 0),
                    "current_price": float(item.get("now_pric2", 0) or 0),
                    "profit_rate": float(item.get("evlu_pfls_rt", 0) or 0),
                })
        return holdings

    def get_order_amount(self) -> float:
        """주문가능금액(USD) 조회"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"
        headers = self.auth.get_auth_headers("TTTS3007R")
        params = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": "NASD",
            "OVRS_ORD_UNPR": "1",
            "ITEM_CD": "",
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        output = data.get("output", {})
        return float(output.get("frcr_ord_psbl_amt1", 0) or 0)

    def sell_market_order(self, symbol: str, quantity: int, exchange: str = "NAS") -> dict:
        """시장가 매도 (손절매용)"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        tr_id = "TTTT1006U"  # 실전투자 해외매도

        exchange_map = {"NYS": "NYSE", "NAS": "NASD", "AMS": "AMEX"}
        headers = self.auth.get_auth_headers(tr_id)

        body = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": exchange_map.get(exchange, "NASD"),
            "PDNO": symbol,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": "0",  # 시장가
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",
        }

        if not IS_REAL_TRADING:
            return {
                "success": True,
                "mode": "simulation",
                "order_no": "VIRTUAL_SELL",
            }

        response = requests.post(url, headers=headers, json=body, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"Sell order failed: {data.get('msg1')}")

        return {
            "success": True,
            "mode": "real",
            "order_no": data.get("output", {}).get("ODNO"),
        }


class SlackBot:
    def __init__(self):
        self.webhook_url = get_env("SLACK_WEBHOOK_URL")

    def send(self, message: str) -> bool:
        if not self.webhook_url:
            print(f"[Slack] {message}")
            return False

        try:
            response = requests.post(
                self.webhook_url,
                json={"text": message},
                timeout=10,
            )
            return response.status_code == 200
        except Exception as e:
            print(f"Slack error: {e}")
            return False


# ========================================
# 매매 전략
# ========================================
def calculate_sma(prices: list, period: int = 20) -> float:
    """단순 이동평균 계산"""
    if len(prices) < period:
        return 0
    return sum(prices[:period]) / period


def calculate_rsi(prices: list, period: int = 14) -> float:
    """
    RSI (Relative Strength Index) 계산
    prices: 최신 가격이 앞에 있는 리스트 (prices[0]이 가장 최근)
    """
    if len(prices) < period + 1:
        return 50  # 데이터 부족 시 중립값 반환

    # 가격 변화량 계산 (최신순이므로 역순으로)
    gains = []
    losses = []

    for i in range(period):
        change = prices[i] - prices[i + 1]  # 오늘 - 어제
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100  # 손실 없음 = RSI 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def check_buy_conditions(current_price: float, sma_20: float, strategy: str,
                         sma_60: float = None, rsi: float = None,
                         use_sma60: bool = False, max_rsi: int = None) -> tuple:
    """
    전략별 매수 조건 판단 (안전장치 포함)

    Returns:
        (bool, str): (매수 여부, 사유)
    """
    if sma_20 == 0:
        return False, "SMA 데이터 부족"

    if strategy == "pullback":
        # 눌림목 매수: 현재가 < 20일 SMA
        if current_price >= sma_20:
            return False, f"SMA20 위에 있음 (${current_price:.2f} >= ${sma_20:.2f})"

        # 추가 조건: 60일 SMA 위에 있어야 함 (장기 추세 확인)
        if use_sma60 and sma_60:
            if current_price < sma_60:
                return False, f"⚠️ 장기추세 하락 (${current_price:.2f} < SMA60 ${sma_60:.2f})"

        return True, f"눌림목 매수 OK (${current_price:.2f} < SMA20 ${sma_20:.2f})"

    elif strategy == "breakout":
        # 반등 매수: 현재가 > 20일 SMA
        if current_price <= sma_20:
            return False, f"SMA20 아래 있음 (${current_price:.2f} <= ${sma_20:.2f})"

        # 추가 조건: RSI가 너무 높지 않아야 함 (과매수 회피)
        if max_rsi and rsi:
            if rsi >= max_rsi:
                return False, f"⚠️ 과매수 구간 (RSI {rsi:.1f} >= {max_rsi})"

        rsi_str = f", RSI {rsi:.1f}" if rsi else ""
        return True, f"반등 매수 OK (${current_price:.2f} > SMA20 ${sma_20:.2f}{rsi_str})"

    else:
        return False, f"알 수 없는 전략: {strategy}"


# ========================================
# 종목별 설정 조회
# ========================================
def get_target_config(symbol: str) -> dict:
    """종목별 설정 조회 (기본값 포함)"""
    for target in TARGETS:
        if target["symbol"] == symbol:
            return {
                "exchange": target.get("exchange", "NYS"),
                "strategy": target.get("strategy", "pullback"),
                "take_profit": target.get("take_profit", 10.0),
                "stop_loss": target.get("stop_loss", -5.0),
                "use_sma60": target.get("use_sma60", False),
                "max_rsi": target.get("max_rsi", None),
            }
    # 기본값 반환
    return {
        "exchange": "NYS",
        "strategy": "pullback",
        "take_profit": 10.0,
        "stop_loss": -5.0,
        "use_sma60": False,
        "max_rsi": None,
    }


# ========================================
# 익절/손절 체크
# ========================================
def check_exit_conditions(overseas: KisOverseas, slack: SlackBot) -> list:
    """보유 종목 익절/손절 체크 (종목별 기준 적용)"""
    print(f"\n{'='*40}")
    print("익절/손절 체크")
    print('='*40)

    results = []

    try:
        holdings = overseas.get_holdings()

        if not holdings:
            print("보유 종목 없음")
            return results

        for holding in holdings:
            symbol = holding["symbol"]
            quantity = holding["quantity"]
            avg_price = holding["avg_price"]
            current_price = holding["current_price"]
            profit_rate = holding["profit_rate"]

            # 종목별 설정 조회
            config = get_target_config(symbol)
            take_profit = config["take_profit"]
            stop_loss = config["stop_loss"]
            exchange = config["exchange"]

            print(f"\n{symbol}: {quantity}주 | 평단가: ${avg_price:.2f} | 현재가: ${current_price:.2f} | 손익: {profit_rate:+.2f}%")
            print(f"  기준: 익절 +{take_profit}% | 손절 {stop_loss}%")

            # 익절 조건 확인
            if profit_rate >= take_profit:
                print(f"  🎉 익절 조건 충족! ({profit_rate:.2f}% >= +{take_profit}%)")

                try:
                    result = overseas.sell_market_order(symbol, quantity, exchange)
                    if result["success"]:
                        msg = f"🎉 익절 달성!\n{symbol} +{profit_rate:.2f}% 수익\n{quantity}주 전량 매도\n주문번호: {result['order_no']}"
                        print(f"  {msg}")
                        slack.send(msg)
                        results.append({"symbol": symbol, "action": "TAKE_PROFIT", "profit_rate": profit_rate})
                    else:
                        print(f"  ❌ 익절 주문 실패")
                        results.append({"symbol": symbol, "action": "TAKE_PROFIT_FAILED"})
                except Exception as e:
                    print(f"  ❌ 익절 주문 오류: {e}")
                    slack.send(f"❌ {symbol} 익절 오류: {e}")
                    results.append({"symbol": symbol, "action": "TAKE_PROFIT_ERROR", "error": str(e)})

            # 손절 조건 확인
            elif profit_rate <= stop_loss:
                print(f"  🚨 손절매 조건 충족! ({profit_rate:.2f}% <= {stop_loss}%)")

                try:
                    result = overseas.sell_market_order(symbol, quantity, exchange)
                    if result["success"]:
                        msg = f"🚨 손절매 발동!\n{symbol} {profit_rate:.2f}% 하락\n{quantity}주 전량 매도\n주문번호: {result['order_no']}"
                        print(f"  {msg}")
                        slack.send(msg)
                        results.append({"symbol": symbol, "action": "STOP_LOSS", "profit_rate": profit_rate})
                    else:
                        print(f"  ❌ 손절매 주문 실패")
                        results.append({"symbol": symbol, "action": "STOP_LOSS_FAILED"})
                except Exception as e:
                    print(f"  ❌ 손절매 주문 오류: {e}")
                    slack.send(f"❌ {symbol} 손절매 오류: {e}")
                    results.append({"symbol": symbol, "action": "STOP_LOSS_ERROR", "error": str(e)})

            else:
                print(f"  ⏳ 홀딩 중 (손절 {stop_loss}% < 현재 {profit_rate:+.2f}% < 익절 +{take_profit}%)")

    except Exception as e:
        print(f"[ERROR] 익절/손절 체크 오류: {e}")
        slack.send(f"❌ 익절/손절 체크 오류: {e}")

    return results


# ========================================
# 단일 종목 매수 처리
# ========================================
def process_buy(overseas: KisOverseas, slack: SlackBot, symbol: str, exchange: str):
    """단일 종목에 대한 매수 로직 실행 (전략별 조건 + 잔고 기반 수량 계산)"""
    # 종목별 설정 조회
    config = get_target_config(symbol)
    strategy = config["strategy"]
    strategy_name = "눌림목" if strategy == "pullback" else "반등"
    use_sma60 = config.get("use_sma60", False)
    max_rsi = config.get("max_rsi", None)

    print(f"\n{'='*40}")
    print(f"매수 체크: {symbol} ({exchange})")
    print(f"전략: {strategy_name} ({strategy})")
    if use_sma60:
        print(f"안전장치: 60일 SMA 체크")
    if max_rsi:
        print(f"안전장치: RSI < {max_rsi}")
    print('='*40)

    try:
        # 1. 현재가 조회
        print(f"[1] 현재가 조회...")
        price_info = overseas.get_current_price(symbol, exchange)
        current_price = price_info["price"]
        print(f"    현재가: ${current_price:.2f}")

        # 2. 이동평균 및 RSI 계산 (필요한 만큼 데이터 조회)
        days_needed = 60 if use_sma60 else 20
        if max_rsi:
            days_needed = max(days_needed, 15)  # RSI는 최소 15일 필요

        print(f"[2] 기술 지표 계산... ({days_needed}일 데이터)")
        daily_prices = overseas.get_daily_prices(symbol, exchange, days_needed)
        print(f"    데이터 수: {len(daily_prices)}일")

        # 20일 SMA
        sma_20 = calculate_sma(daily_prices, 20)
        print(f"    20일 SMA: ${sma_20:.2f}")

        # 60일 SMA (필요시)
        sma_60 = None
        if use_sma60:
            sma_60 = calculate_sma(daily_prices, 60)
            if sma_60 > 0:
                print(f"    60일 SMA: ${sma_60:.2f}")
            else:
                print(f"    60일 SMA: 데이터 부족")

        # RSI (필요시)
        rsi = None
        if max_rsi:
            rsi = calculate_rsi(daily_prices, 14)
            print(f"    RSI(14): {rsi:.1f}")

        # 3. 매수 조건 확인 (안전장치 포함)
        print(f"[3] 매수 조건 확인 ({strategy_name} 전략)...")
        buy_signal, reason = check_buy_conditions(
            current_price=current_price,
            sma_20=sma_20,
            strategy=strategy,
            sma_60=sma_60,
            rsi=rsi,
            use_sma60=use_sma60,
            max_rsi=max_rsi
        )
        print(f"    결과: {buy_signal} - {reason}")

        # 4. 주문 실행
        if buy_signal:
            # 잔고 기반 수량 계산
            print(f"[4] 잔고 기반 수량 계산...")
            try:
                available_usd = overseas.get_order_amount()
                print(f"    주문가능금액: ${available_usd:.2f}")

                # 최대 몇 주 살 수 있는지 계산
                final_quantity = int(available_usd / current_price)
                print(f"    계산: ${available_usd:.2f} / ${current_price:.2f} = {final_quantity}주 가능")

                if final_quantity < 1:
                    print(f"    💸 잔고 부족으로 매수 불가 (최소 1주 필요: ${current_price:.2f})")
                    return {"symbol": symbol, "action": "NO_BALANCE", "price": current_price, "available": available_usd}

            except Exception as e:
                print(f"    잔고 조회 실패: {e}")
                return {"symbol": symbol, "action": "ERROR", "error": str(e)}

            print(f"[5] 시장가 매수 주문... ({final_quantity}주)")
            try:
                result = overseas.buy_market_order(symbol, final_quantity, exchange)

                if result["success"]:
                    msg = f"✅ [{result['mode']}] {symbol} {final_quantity}주 시장가 매수!\n주문번호: {result['order_no']}"
                    print(f"    {msg}")
                    slack.send(msg)
                    return {"symbol": symbol, "action": "BUY", "price": current_price, "quantity": final_quantity}
                else:
                    msg = f"❌ {symbol} 매수 실패"
                    print(f"    {msg}")
                    slack.send(msg)
                    return {"symbol": symbol, "action": "FAILED", "price": current_price}

            except ValueError as e:
                error_msg = str(e)
                # 잔고 부족 체크
                if "잔고" in error_msg or "금액" in error_msg or "부족" in error_msg:
                    print(f"    💸 잔고 부족으로 패스: {error_msg}")
                    return {"symbol": symbol, "action": "NO_BALANCE", "price": current_price}
                else:
                    raise
        else:
            print(f"[4] 매수 조건 미충족 - 패스")
            return {"symbol": symbol, "action": "SKIP", "price": current_price, "sma": sma_20, "reason": reason}

    except Exception as e:
        print(f"[ERROR] {symbol} 처리 중 오류: {e}")
        slack.send(f"❌ {symbol} 오류: {e}")
        return {"symbol": symbol, "action": "ERROR", "error": str(e)}


# ========================================
# 매매 기록 저장
# ========================================
def save_trade_history(results: list):
    """매매 결과를 JSON 파일에 저장"""
    try:
        # 기존 기록 로드
        history = []
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE, "r") as f:
                history = json.load(f)

        # 새 기록 추가
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for result in results:
            if result.get("action") in ["BUY", "TAKE_PROFIT", "STOP_LOSS"]:
                record = {
                    "timestamp": timestamp,
                    "symbol": result.get("symbol"),
                    "action": result.get("action"),
                    "price": result.get("price"),
                    "quantity": result.get("quantity"),
                    "profit_rate": result.get("profit_rate"),
                }
                history.append(record)

        # 최근 100개만 유지
        history = history[-100:]

        # 저장
        with open(TRADE_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)

        print(f"[기록] 매매 기록 저장 완료 ({len(history)}건)")

    except Exception as e:
        print(f"[기록] 저장 실패: {e}")


# ========================================
# 메인 실행
# ========================================
def main():
    now = datetime.now()
    mode_str = "🔴 실전" if IS_REAL_TRADING else "🟢 시뮬레이션"

    print("=" * 50)
    print(f"자동 매매 실행 ({mode_str})")
    print(f"시간: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 종목별 전략 출력
    strategy_lines = []
    for t in TARGETS:
        s = t.get("strategy", "pullback")
        s_name = "눌림목" if s == "pullback" else "반등"
        tp = t.get("take_profit", 10.0)
        sl = t.get("stop_loss", -5.0)
        line = f"{t['symbol']}: {s_name} (익절 +{tp}%, 손절 {sl}%)"
        strategy_lines.append(line)
        print(f"  {line}")

    print("=" * 50)

    slack = SlackBot()
    slack.send(f"🤖 자동매매 시작 ({mode_str})\n" + "\n".join(strategy_lines))

    try:
        # 1. 인증
        print("\n[인증] API 토큰 발급...")
        auth = KisAuth()
        auth.get_access_token()
        overseas = KisOverseas(auth)
        print("[인증] 완료")

        # 2. 익절/손절 체크 (먼저 실행)
        exit_results = check_exit_conditions(overseas, slack)

        # 3. 각 종목 매수 체크
        buy_results = []
        for target in TARGETS:
            result = process_buy(
                overseas=overseas,
                slack=slack,
                symbol=target["symbol"],
                exchange=target["exchange"],
            )
            buy_results.append(result)

        # 4. 결과 요약
        print("\n" + "=" * 50)
        print("실행 결과 요약")
        print("=" * 50)

        summary_lines = []

        # 익절/손절 결과
        for r in exit_results:
            if r["action"] == "TAKE_PROFIT":
                line = f"🎉 {r['symbol']}: 익절 (+{r['profit_rate']:.2f}%)"
                summary_lines.append(line)
            elif r["action"] == "STOP_LOSS":
                line = f"🚨 {r['symbol']}: 손절 ({r['profit_rate']:.2f}%)"
                summary_lines.append(line)

        # 매수 결과
        for r in buy_results:
            if r["action"] == "BUY":
                qty = r.get("quantity", 1)
                line = f"✅ {r['symbol']}: {qty}주 매수 @ ${r['price']:.2f}"
            elif r["action"] == "SKIP":
                reason = r.get("reason", "조건 미충족")
                line = f"⏸️ {r['symbol']}: 패스 ({reason})"
            elif r["action"] == "NO_BALANCE":
                avail = r.get("available", 0)
                line = f"💸 {r['symbol']}: 잔고 부족 (${avail:.2f} < ${r['price']:.2f})"
            elif r["action"] == "ERROR":
                line = f"❌ {r['symbol']}: 오류"
            else:
                line = f"❌ {r['symbol']}: 실패"
            summary_lines.append(line)

        for line in summary_lines:
            print(line)

        # 슬랙 요약 전송
        slack.send("📊 자동매매 완료\n" + "\n".join(summary_lines))

        # 매매 기록 저장
        save_trade_history(exit_results + buy_results)

        print("\n" + "=" * 50)
        print("자동 매매 완료")
        print("=" * 50)

    except Exception as e:
        error_msg = f"❌ 자동매매 오류: {e}"
        print(f"\n{error_msg}")
        slack.send(error_msg)
        raise


if __name__ == "__main__":
    main()
