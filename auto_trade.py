"""
자동 매매 스크립트
GitHub Actions에서 정기 실행

전략: VRT(버티브) 현재가가 20일 이동평균선 아래면 1주 매수
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv

# ========================================
# 설정
# ========================================
SYMBOL = "VRT"
EXCHANGE = "NYS"
QUANTITY = 1
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

    def buy_limit_order(self, symbol: str, quantity: int, price: float, exchange: str = "NYS") -> dict:
        """지정가 매수"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        tr_id = "JTTT1002U"

        exchange_map = {"NYS": "NYSE", "NAS": "NASD", "AMS": "AMEX"}
        headers = self.auth.get_auth_headers(tr_id)

        body = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": exchange_map.get(exchange, "NYSE"),
            "PDNO": symbol,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
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


def should_buy(current_price: float, sma_20: float) -> bool:
    """
    매수 조건: 현재가가 20일 이평선 아래면 매수
    """
    if sma_20 == 0:
        return False
    return current_price < sma_20


# ========================================
# 메인 실행
# ========================================
def main():
    now = datetime.now()
    mode_str = "🔴 실전" if IS_REAL_TRADING else "🟢 시뮬레이션"

    print("=" * 50)
    print(f"자동 매매 실행 ({mode_str})")
    print(f"시간: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"종목: {SYMBOL} ({EXCHANGE})")
    print("=" * 50)

    slack = SlackBot()
    slack.send(f"🤖 자동매매 시작 ({mode_str})\n종목: {SYMBOL}")

    try:
        # 1. 인증
        print("\n[1] API 인증...")
        auth = KisAuth()
        auth.get_access_token()
        overseas = KisOverseas(auth)
        print("    인증 완료")

        # 2. 현재가 조회
        print(f"\n[2] {SYMBOL} 현재가 조회...")
        price_info = overseas.get_current_price(SYMBOL, EXCHANGE)
        current_price = price_info["price"]
        print(f"    현재가: ${current_price:.2f}")

        # 3. 20일 이동평균 계산
        print(f"\n[3] 20일 이동평균 계산...")
        daily_prices = overseas.get_daily_prices(SYMBOL, EXCHANGE, 20)
        sma_20 = calculate_sma(daily_prices, 20)
        print(f"    20일 SMA: ${sma_20:.2f}")
        print(f"    데이터 수: {len(daily_prices)}일")

        # 4. 매수 조건 확인
        print(f"\n[4] 매수 조건 확인...")
        buy_signal = should_buy(current_price, sma_20)
        print(f"    현재가 < 20SMA: {current_price:.2f} < {sma_20:.2f} = {buy_signal}")

        # 5. 주문 실행
        if buy_signal:
            print(f"\n[5] 매수 주문 실행...")
            result = overseas.buy_limit_order(SYMBOL, QUANTITY, current_price, EXCHANGE)

            if result["success"]:
                msg = f"✅ [{result['mode']}] {SYMBOL} {QUANTITY}주 매수 주문!\n가격: ${current_price:.2f}\n조건: 현재가({current_price:.2f}) < 20SMA({sma_20:.2f})"
                print(f"    {msg}")
                slack.send(msg)
            else:
                msg = f"❌ {SYMBOL} 매수 주문 실패"
                print(f"    {msg}")
                slack.send(msg)
        else:
            msg = f"⏸️ {SYMBOL} 매수 조건 미충족\n현재가: ${current_price:.2f}\n20SMA: ${sma_20:.2f}\n(현재가가 이평선 위에 있음)"
            print(f"\n[5] {msg}")
            slack.send(msg)

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
