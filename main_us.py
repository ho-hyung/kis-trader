"""
미국 주식 자동매매 MVP
Ford(F) 1주 지정가 매수
"""

import os
import requests
from dotenv import load_dotenv

# ========================================
# 안전 장치: False면 가상 주문, True면 실제 주문
# ========================================
IS_REAL_TRADING = False


class KisAuth:
    """한국투자증권 인증 클래스"""

    BASE_URL = "https://openapi.koreainvestment.com:9443"

    def __init__(self):
        load_dotenv()

        self.app_key = os.getenv("KIS_APP_KEY")
        self.app_secret = os.getenv("KIS_APP_SECRET")
        self.account_number = os.getenv("KIS_ACCOUNT_NUMBER")
        self.account_product_code = os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01")

        self._validate_credentials()
        self.access_token = None

    def _validate_credentials(self):
        """필수 환경변수 검증"""
        missing = []
        if not self.app_key:
            missing.append("KIS_APP_KEY")
        if not self.app_secret:
            missing.append("KIS_APP_SECRET")
        if not self.account_number:
            missing.append("KIS_ACCOUNT_NUMBER")

        if missing:
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")

    def get_access_token(self) -> str:
        """접속 토큰 발급"""
        url = f"{self.BASE_URL}/oauth2/tokenP"
        headers = {"Content-Type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        try:
            response = requests.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()

            data = response.json()
            self.access_token = data.get("access_token")

            if not self.access_token:
                raise ValueError(f"Token not found in response: {data}")

            return self.access_token

        except requests.RequestException as e:
            raise RuntimeError(f"Token request failed: {e}")

    def get_auth_headers(self, tr_id: str) -> dict:
        """인증 헤더 생성"""
        if not self.access_token:
            raise ValueError("Access token not available. Call get_access_token() first.")

        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }


class KisOverseas:
    """해외주식(미국) 거래 클래스"""

    def __init__(self, auth: KisAuth):
        self.auth = auth
        self.base_url = auth.BASE_URL

    def get_current_price(self, symbol: str, exchange: str = "NYS") -> dict:
        """
        해외주식 현재가 조회

        Args:
            symbol: 종목코드 (예: "F", "AAPL")
            exchange: 거래소 코드 (NYS=뉴욕, NAS=나스닥, AMS=아멕스)

        Returns:
            현재가 정보
        """
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        tr_id = "HHDFS00000300"

        headers = self.auth.get_auth_headers(tr_id)
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if data.get("rt_cd") != "0":
                raise ValueError(f"API error: {data.get('msg1')}")

            output = data.get("output", {})
            last_price = output.get("last", "")

            return {
                "symbol": symbol,
                "exchange": exchange,
                "price": float(last_price) if last_price else 0.0,
                "change_rate": float(output.get("rate", 0) or 0),
                "raw": output,
            }

        except requests.RequestException as e:
            raise RuntimeError(f"Price request failed: {e}")

    def buy_limit_order(self, symbol: str, quantity: int, price: float, exchange: str = "NYS") -> dict:
        """
        해외주식 지정가 매수

        Args:
            symbol: 종목코드
            quantity: 주문 수량
            price: 지정가격 (USD)
            exchange: 거래소 코드

        Returns:
            주문 결과
        """
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        tr_id = "JTTT1002U"  # 미국 매수 주문

        # 거래소 코드 매핑
        exchange_map = {"NYS": "NYSE", "NAS": "NASD", "AMS": "AMEX"}
        ovrs_excg_cd = exchange_map.get(exchange, "NYSE")

        headers = self.auth.get_auth_headers(tr_id)
        body = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": ovrs_excg_cd,
            "PDNO": symbol,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # 지정가
        }

        print(f"[US] Limit buy order: {symbol} x {quantity} @ ${price:.2f} ({exchange})")

        if not IS_REAL_TRADING:
            print(">>> 가상 주문 전송됨 (IS_REAL_TRADING = False)")
            return {
                "success": True,
                "mode": "simulation",
                "order_no": "VIRTUAL_ORDER",
                "message": "가상 주문이 전송되었습니다.",
                "request_body": body,
            }

        # 실제 주문 전송
        try:
            response = requests.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("rt_cd") != "0":
                raise ValueError(f"Order failed: {data.get('msg1')}")

            return {
                "success": True,
                "mode": "real",
                "order_no": data.get("output", {}).get("ODNO"),
                "raw": data,
            }
        except requests.RequestException as e:
            raise RuntimeError(f"Order request failed: {e}")


class SlackBot:
    """Slack Webhook 알림 클래스"""

    def __init__(self):
        load_dotenv()
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL")

        if not self.webhook_url:
            print("[SlackBot] Warning: SLACK_WEBHOOK_URL not configured")

    def send(self, message: str) -> bool:
        """슬랙으로 메시지 전송"""
        if not self.webhook_url:
            print(f"[SlackBot] (No webhook) {message}")
            return False

        try:
            response = requests.post(
                self.webhook_url,
                json={"text": message},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            return True

        except requests.RequestException as e:
            print(f"[SlackBot] Failed to send message: {e}")
            return False


class TradingBot:
    """미국 주식 자동매매 봇"""

    def __init__(self):
        self.slack = SlackBot()
        self.auth = KisAuth()
        self.overseas = KisOverseas(self.auth)

    def run(self, symbol: str = "F", exchange: str = "NYS", quantity: int = 1):
        """
        자동매매 실행

        Args:
            symbol: 종목코드 (기본: Ford)
            exchange: 거래소 (기본: NYSE)
            quantity: 매수 수량 (기본: 1주)
        """
        mode_str = "🔴 실전" if IS_REAL_TRADING else "🟢 시뮬레이션"
        print("=" * 50)
        print(f"미국 주식 자동매매 봇 ({mode_str})")
        print("=" * 50)

        # 1. 슬랙 알림 - 시작
        self.slack.send(f"🇺🇸 미국주식 봇 가동! ({mode_str} 모드)\n대상: {symbol} ({exchange})")

        try:
            # 2. 토큰 발급
            print("\n[1] 토큰 발급 중...")
            self.auth.get_access_token()
            print("    토큰 발급 완료")

            # 3. 현재가 조회
            print(f"\n[2] {symbol} 현재가 조회 중...")
            price_info = self.overseas.get_current_price(symbol, exchange)
            current_price = price_info["price"]
            change_rate = price_info["change_rate"]

            print(f"    현재가: ${current_price:.2f} ({change_rate:+.2f}%)")
            self.slack.send(
                f"📊 {symbol} 현재가: ${current_price:.2f} ({change_rate:+.2f}%)"
            )

            # 4. 지정가 매수 주문
            print(f"\n[3] {symbol} {quantity}주 지정가 매수 주문...")
            order_result = self.overseas.buy_limit_order(
                symbol=symbol,
                quantity=quantity,
                price=current_price,
                exchange=exchange,
            )

            if order_result["success"]:
                order_no = order_result["order_no"]
                mode = order_result["mode"]

                if mode == "simulation":
                    msg = f"✅ [시뮬레이션] {symbol} {quantity}주 매수 주문 (${current_price:.2f})"
                else:
                    msg = f"✅ [실전] {symbol} {quantity}주 매수 주문 완료!\n주문번호: {order_no}\n가격: ${current_price:.2f}"

                print(f"    {msg}")
                self.slack.send(msg)
            else:
                msg = f"❌ {symbol} 매수 주문 실패"
                print(f"    {msg}")
                self.slack.send(msg)

            print("\n" + "=" * 50)
            print("자동매매 완료!")
            print("=" * 50)

        except Exception as e:
            error_msg = f"❌ 오류 발생: {e}"
            print(f"\n{error_msg}")
            self.slack.send(error_msg)
            raise


if __name__ == "__main__":
    print(f"\n*** IS_REAL_TRADING = {IS_REAL_TRADING} ***\n")

    bot = TradingBot()
    bot.run(symbol="F", exchange="NYS", quantity=1)
