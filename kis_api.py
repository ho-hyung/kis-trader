"""
한국투자증권(KIS) Open API 연동 클래스
실전투자 서버 기준
"""

import os
import requests
from dotenv import load_dotenv


class KisApi:
    """한국투자증권 Open API 클라이언트"""

    # 실전투자 서버 Base URL
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
        """접속 토큰 발급 (실전투자)"""
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

            print("Access token issued successfully")
            return self.access_token

        except requests.RequestException as e:
            print(f"Token request failed: {e}")
            raise

    def _get_auth_headers(self, tr_id: str) -> dict:
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

    def get_current_price(self, market: str, code: str, exchange: str = "NAS") -> dict:
        """
        현재가 조회

        Args:
            market: "KR" (국내) 또는 "US" (미국)
            code: 종목코드 (예: "005930", "VRT")
            exchange: 해외 거래소 코드 (NAS=나스닥, NYS=뉴욕, AMS=아멕스)

        Returns:
            현재가 정보 딕셔너리
        """
        if market.upper() == "KR":
            return self._get_kr_current_price(code)
        elif market.upper() == "US":
            return self._get_us_current_price(code, exchange)
        else:
            raise ValueError(f"Unsupported market: {market}. Use 'KR' or 'US'.")

    def _get_kr_stock_name(self, code: str) -> str:
        """국내주식 종목명 조회"""
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/quotations/search-stock-info"
        tr_id = "CTPF1002R"

        headers = self._get_auth_headers(tr_id)
        params = {
            "PRDT_TYPE_CD": "300",
            "PDNO": code,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("rt_cd") == "0":
                return data.get("output", {}).get("prdt_abrv_name", code)
        except Exception:
            pass

        return code

    def _get_kr_current_price(self, code: str) -> dict:
        """국내주식 현재가 조회"""
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        tr_id = "FHKST01010100"

        headers = self._get_auth_headers(tr_id)
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",  # 주식
            "FID_INPUT_ISCD": code,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if data.get("rt_cd") != "0":
                raise ValueError(f"API error: {data.get('msg1')}")

            output = data.get("output", {})
            stock_name = self._get_kr_stock_name(code)

            return {
                "market": "KR",
                "code": code,
                "name": stock_name,
                "current_price": int(output.get("stck_prpr", 0)),
                "change_rate": float(output.get("prdy_ctrt", 0)),
                "volume": int(output.get("acml_vol", 0)),
                "raw": output,
            }

        except requests.RequestException as e:
            print(f"KR price request failed: {e}")
            raise

    def _get_us_current_price(self, code: str, exchange: str = "NAS") -> dict:
        """
        해외주식(미국) 현재가 조회

        Args:
            code: 종목코드
            exchange: 거래소 코드 (NAS=나스닥, NYS=뉴욕, AMS=아멕스)
        """
        url = f"{self.BASE_URL}/uapi/overseas-price/v1/quotations/price"
        tr_id = "HHDFS00000300"

        headers = self._get_auth_headers(tr_id)
        params = {
            "AUTH": "",
            "EXCD": exchange.upper(),
            "SYMB": code,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if data.get("rt_cd") != "0":
                raise ValueError(f"API error: {data.get('msg1')}")

            output = data.get("output", {})

            # 빈 문자열 처리
            last_price = output.get("last", "")
            rate = output.get("rate", "")
            tvol = output.get("tvol", "")

            return {
                "market": "US",
                "code": code,
                "exchange": exchange.upper(),
                "name": output.get("rsym", "N/A"),
                "current_price": float(last_price) if last_price else 0.0,
                "change_rate": float(rate) if rate else 0.0,
                "volume": int(tvol) if tvol else 0,
                "raw": output,
            }

        except requests.RequestException as e:
            print(f"US price request failed: {e}")
            raise

    def get_balance(self, market: str = "KR") -> dict:
        """
        주식 잔고 조회

        Args:
            market: "KR" (국내) 또는 "US" (미국)

        Returns:
            잔고 정보 딕셔너리
        """
        if market.upper() == "KR":
            return self._get_kr_balance()
        elif market.upper() == "US":
            return self._get_us_balance()
        else:
            raise ValueError(f"Unsupported market: {market}")

    def _get_kr_balance(self) -> dict:
        """국내주식 잔고 조회"""
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        tr_id = "TTTC8434R"  # 실전투자 잔고조회

        headers = self._get_auth_headers(tr_id)
        params = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if data.get("rt_cd") != "0":
                raise ValueError(f"API error: {data.get('msg1')}")

            holdings = []
            for item in data.get("output1", []):
                holdings.append({
                    "code": item.get("pdno"),
                    "name": item.get("prdt_name"),
                    "quantity": int(item.get("hldg_qty", 0)),
                    "avg_price": float(item.get("pchs_avg_pric", 0)),
                    "current_price": int(item.get("prpr", 0)),
                    "profit_rate": float(item.get("evlu_pfls_rt", 0)),
                })

            summary = data.get("output2", [{}])[0] if data.get("output2") else {}

            return {
                "market": "KR",
                "holdings": holdings,
                "total_eval": int(summary.get("tot_evlu_amt", 0)),
                "total_profit": int(summary.get("evlu_pfls_smtl_amt", 0)),
                "raw": data,
            }

        except requests.RequestException as e:
            print(f"KR balance request failed: {e}")
            raise

    def _get_us_balance(self) -> dict:
        """해외주식 잔고 조회"""
        url = f"{self.BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance"
        tr_id = "TTTS3012R"  # 실전투자 해외잔고조회

        headers = self._get_auth_headers(tr_id)
        params = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if data.get("rt_cd") != "0":
                raise ValueError(f"API error: {data.get('msg1')}")

            holdings = []
            for item in data.get("output1", []):
                holdings.append({
                    "code": item.get("ovrs_pdno"),
                    "name": item.get("ovrs_item_name"),
                    "quantity": int(item.get("ovrs_cblc_qty", 0)),
                    "avg_price": float(item.get("pchs_avg_pric", 0)),
                    "current_price": float(item.get("now_pric2", 0)),
                    "profit_rate": float(item.get("evlu_pfls_rt", 0)),
                })

            return {
                "market": "US",
                "holdings": holdings,
                "raw": data,
            }

        except requests.RequestException as e:
            print(f"US balance request failed: {e}")
            raise

    def buy_market_order(self, market: str, code: str, quantity: int) -> dict:
        """
        시장가 매수 주문

        Args:
            market: "KR" (국내) 또는 "US" (미국)
            code: 종목코드
            quantity: 주문 수량

        Returns:
            주문 결과 딕셔너리
        """
        if market.upper() == "KR":
            return self._buy_kr_market_order(code, quantity)
        elif market.upper() == "US":
            return self._buy_us_market_order(code, quantity)
        else:
            raise ValueError(f"Unsupported market: {market}")

    def _buy_kr_market_order(self, code: str, quantity: int) -> dict:
        """국내주식 시장가 매수"""
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "TTTC0802U"  # 실전투자 매수

        headers = self._get_auth_headers(tr_id)
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "PDNO": code,
            "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",  # 시장가는 0
        }

        print(f"[KR] Market buy order: {code} x {quantity}")
        print("Order request prepared (actual POST is commented out for safety)")

        # ================================================
        # 안전을 위해 실제 주문 요청은 주석 처리
        # 실제 사용 시 아래 주석을 해제하세요
        # ================================================
        # try:
        #     response = requests.post(url, headers=headers, json=body, timeout=10)
        #     response.raise_for_status()
        #     data = response.json()
        #
        #     if data.get("rt_cd") != "0":
        #         raise ValueError(f"Order failed: {data.get('msg1')}")
        #
        #     return {
        #         "success": True,
        #         "order_no": data.get("output", {}).get("ODNO"),
        #         "raw": data,
        #     }
        # except requests.RequestException as e:
        #     print(f"KR order request failed: {e}")
        #     raise

        return {
            "success": False,
            "message": "Order not executed (commented out for safety)",
            "request_body": body,
        }

    def _buy_us_market_order(self, code: str, quantity: int) -> dict:
        """해외주식(미국) 시장가 매수"""
        url = f"{self.BASE_URL}/uapi/overseas-stock/v1/trading/order"
        tr_id = "TTTT1002U"  # 실전투자 해외매수

        headers = self._get_auth_headers(tr_id)
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": "NASD",
            "PDNO": code,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # 시장가
        }

        print(f"[US] Market buy order: {code} x {quantity}")
        print("Order request prepared (actual POST is commented out for safety)")

        # ================================================
        # 안전을 위해 실제 주문 요청은 주석 처리
        # 실제 사용 시 아래 주석을 해제하세요
        # ================================================
        # try:
        #     response = requests.post(url, headers=headers, json=body, timeout=10)
        #     response.raise_for_status()
        #     data = response.json()
        #
        #     if data.get("rt_cd") != "0":
        #         raise ValueError(f"Order failed: {data.get('msg1')}")
        #
        #     return {
        #         "success": True,
        #         "order_no": data.get("output", {}).get("ODNO"),
        #         "raw": data,
        #     }
        # except requests.RequestException as e:
        #     print(f"US order request failed: {e}")
        #     raise

        return {
            "success": False,
            "message": "Order not executed (commented out for safety)",
            "request_body": body,
        }

    def buy_limit_order(self, market: str, code: str, quantity: int, price: float, exchange: str = "NASD") -> dict:
        """
        지정가 매수 주문

        Args:
            market: "KR" (국내) 또는 "US" (미국)
            code: 종목코드
            quantity: 주문 수량
            price: 지정가격
            exchange: 해외 거래소 코드 (NASD, NYSE, AMEX)

        Returns:
            주문 결과 딕셔너리
        """
        if market.upper() == "KR":
            return self._buy_kr_limit_order(code, quantity, int(price))
        elif market.upper() == "US":
            return self._buy_us_limit_order(code, quantity, price, exchange)
        else:
            raise ValueError(f"Unsupported market: {market}")

    def _buy_kr_limit_order(self, code: str, quantity: int, price: int) -> dict:
        """국내주식 지정가 매수"""
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "TTTC0802U"  # 실전투자 매수

        headers = self._get_auth_headers(tr_id)
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "PDNO": code,
            "ORD_DVSN": "00",  # 지정가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }

        print(f"[KR] Limit buy order: {code} x {quantity} @ {price:,}원")

        try:
            response = requests.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("rt_cd") != "0":
                raise ValueError(f"Order failed: {data.get('msg1')}")

            return {
                "success": True,
                "order_no": data.get("output", {}).get("ODNO"),
                "raw": data,
            }
        except requests.RequestException as e:
            print(f"KR order request failed: {e}")
            raise

    def _buy_us_limit_order(self, code: str, quantity: int, price: float, exchange: str = "NASD") -> dict:
        """해외주식(미국) 지정가 매수"""
        url = f"{self.BASE_URL}/uapi/overseas-stock/v1/trading/order"
        tr_id = "TTTT1002U"  # 실전투자 해외매수

        headers = self._get_auth_headers(tr_id)
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": exchange,
            "PDNO": code,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # 지정가
        }

        print(f"[US] Limit buy order: {code} x {quantity} @ ${price:.2f} ({exchange})")
        print("Order request prepared (actual POST is commented out for safety)")

        # ================================================
        # 안전을 위해 실제 주문 요청은 주석 처리
        # 실제 사용 시 아래 주석을 해제하세요
        # ================================================
        # try:
        #     response = requests.post(url, headers=headers, json=body, timeout=10)
        #     response.raise_for_status()
        #     data = response.json()
        #
        #     if data.get("rt_cd") != "0":
        #         raise ValueError(f"Order failed: {data.get('msg1')}")
        #
        #     return {
        #         "success": True,
        #         "order_no": data.get("output", {}).get("ODNO"),
        #         "raw": data,
        #     }
        # except requests.RequestException as e:
        #     print(f"US order request failed: {e}")
        #     raise

        return {
            "success": False,
            "message": "Order not executed (commented out for safety)",
            "request_body": body,
        }

    # ========================================
    # 매도 주문
    # ========================================

    def sell_market_order(self, market: str, code: str, quantity: int) -> dict:
        """
        시장가 매도 주문

        Args:
            market: "KR" (국내) 또는 "US" (미국)
            code: 종목코드
            quantity: 주문 수량

        Returns:
            주문 결과 딕셔너리
        """
        if market.upper() == "KR":
            return self._sell_kr_market_order(code, quantity)
        elif market.upper() == "US":
            return self._sell_us_market_order(code, quantity)
        else:
            raise ValueError(f"Unsupported market: {market}")

    def _sell_kr_market_order(self, code: str, quantity: int) -> dict:
        """국내주식 시장가 매도"""
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "TTTC0801U"  # 실전투자 매도

        headers = self._get_auth_headers(tr_id)
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "PDNO": code,
            "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
        }

        print(f"[KR] Market sell order: {code} x {quantity}")
        print("Order request prepared (actual POST is commented out for safety)")

        # ================================================
        # 안전을 위해 실제 주문 요청은 주석 처리
        # 실제 사용 시 아래 주석을 해제하세요
        # ================================================
        # try:
        #     response = requests.post(url, headers=headers, json=body, timeout=10)
        #     response.raise_for_status()
        #     data = response.json()
        #
        #     if data.get("rt_cd") != "0":
        #         raise ValueError(f"Order failed: {data.get('msg1')}")
        #
        #     return {
        #         "success": True,
        #         "order_no": data.get("output", {}).get("ODNO"),
        #         "raw": data,
        #     }
        # except requests.RequestException as e:
        #     print(f"KR order request failed: {e}")
        #     raise

        return {
            "success": False,
            "message": "Order not executed (commented out for safety)",
            "request_body": body,
        }

    def _sell_us_market_order(self, code: str, quantity: int) -> dict:
        """해외주식(미국) 시장가 매도"""
        url = f"{self.BASE_URL}/uapi/overseas-stock/v1/trading/order"
        tr_id = "TTTT1006U"  # 실전투자 해외매도

        headers = self._get_auth_headers(tr_id)
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": "NASD",
            "PDNO": code,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # 시장가
        }

        print(f"[US] Market sell order: {code} x {quantity}")
        print("Order request prepared (actual POST is commented out for safety)")

        # ================================================
        # 안전을 위해 실제 주문 요청은 주석 처리
        # 실제 사용 시 아래 주석을 해제하세요
        # ================================================
        # try:
        #     response = requests.post(url, headers=headers, json=body, timeout=10)
        #     response.raise_for_status()
        #     data = response.json()
        #
        #     if data.get("rt_cd") != "0":
        #         raise ValueError(f"Order failed: {data.get('msg1')}")
        #
        #     return {
        #         "success": True,
        #         "order_no": data.get("output", {}).get("ODNO"),
        #         "raw": data,
        #     }
        # except requests.RequestException as e:
        #     print(f"US order request failed: {e}")
        #     raise

        return {
            "success": False,
            "message": "Order not executed (commented out for safety)",
            "request_body": body,
        }

    def sell_limit_order(self, market: str, code: str, quantity: int, price: float, exchange: str = "NASD") -> dict:
        """
        지정가 매도 주문

        Args:
            market: "KR" (국내) 또는 "US" (미국)
            code: 종목코드
            quantity: 주문 수량
            price: 지정가격
            exchange: 해외 거래소 코드 (NASD, NYSE, AMEX)

        Returns:
            주문 결과 딕셔너리
        """
        if market.upper() == "KR":
            return self._sell_kr_limit_order(code, quantity, int(price))
        elif market.upper() == "US":
            return self._sell_us_limit_order(code, quantity, price, exchange)
        else:
            raise ValueError(f"Unsupported market: {market}")

    def _sell_kr_limit_order(self, code: str, quantity: int, price: int) -> dict:
        """국내주식 지정가 매도"""
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "TTTC0801U"  # 실전투자 매도

        headers = self._get_auth_headers(tr_id)
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "PDNO": code,
            "ORD_DVSN": "00",  # 지정가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }

        print(f"[KR] Limit sell order: {code} x {quantity} @ {price:,}원")
        print("Order request prepared (actual POST is commented out for safety)")

        # ================================================
        # 안전을 위해 실제 주문 요청은 주석 처리
        # 실제 사용 시 아래 주석을 해제하세요
        # ================================================
        # try:
        #     response = requests.post(url, headers=headers, json=body, timeout=10)
        #     response.raise_for_status()
        #     data = response.json()
        #
        #     if data.get("rt_cd") != "0":
        #         raise ValueError(f"Order failed: {data.get('msg1')}")
        #
        #     return {
        #         "success": True,
        #         "order_no": data.get("output", {}).get("ODNO"),
        #         "raw": data,
        #     }
        # except requests.RequestException as e:
        #     print(f"KR order request failed: {e}")
        #     raise

        return {
            "success": False,
            "message": "Order not executed (commented out for safety)",
            "request_body": body,
        }

    def _sell_us_limit_order(self, code: str, quantity: int, price: float, exchange: str = "NASD") -> dict:
        """해외주식(미국) 지정가 매도"""
        url = f"{self.BASE_URL}/uapi/overseas-stock/v1/trading/order"
        tr_id = "TTTT1006U"  # 실전투자 해외매도

        headers = self._get_auth_headers(tr_id)
        body = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": exchange,
            "PDNO": code,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # 지정가
        }

        print(f"[US] Limit sell order: {code} x {quantity} @ ${price:.2f} ({exchange})")
        print("Order request prepared (actual POST is commented out for safety)")

        # ================================================
        # 안전을 위해 실제 주문 요청은 주석 처리
        # 실제 사용 시 아래 주석을 해제하세요
        # ================================================
        # try:
        #     response = requests.post(url, headers=headers, json=body, timeout=10)
        #     response.raise_for_status()
        #     data = response.json()
        #
        #     if data.get("rt_cd") != "0":
        #         raise ValueError(f"Order failed: {data.get('msg1')}")
        #
        #     return {
        #         "success": True,
        #         "order_no": data.get("output", {}).get("ODNO"),
        #         "raw": data,
        #     }
        # except requests.RequestException as e:
        #     print(f"US order request failed: {e}")
        #     raise

        return {
            "success": False,
            "message": "Order not executed (commented out for safety)",
            "request_body": body,
        }


if __name__ == "__main__":
    from slack_bot import SlackBot

    # 테스트 실행
    print("=" * 50)
    print("KIS API Test")
    print("=" * 50)

    # SlackBot 초기화
    slack = SlackBot()
    slack.send("🚀 슬랙 알림 시스템 가동! (Slack Bot Connected)")

    try:
        # API 클라이언트 초기화
        api = KisApi()

        # 1. 토큰 발급
        print("\n[1] Getting access token...")
        api.get_access_token()

        # 2. 국내주식 현재가 조회 (삼성전자)
        print("\n[2] Getting Samsung Electronics (005930) price...")
        kr_price = api.get_current_price("KR", "005930")
        print(f"  Code: {kr_price['code']}")
        print(f"  Current Price: {kr_price['current_price']:,} KRW")
        print(f"  Change Rate: {kr_price['change_rate']}%")
        print(f"  Volume: {kr_price['volume']:,}")

        # 3. 해외주식 현재가 조회 (Vertiv - NYSE 상장)
        print("\n[3] Getting Vertiv (VRT) price...")
        us_price = api.get_current_price("US", "VRT", exchange="NYS")
        print(f"  Code: {us_price['code']} ({us_price['exchange']})")
        print(f"  Current Price: ${us_price['current_price']:.2f}")
        print(f"  Change Rate: {us_price['change_rate']}%")
        print(f"  Volume: {us_price['volume']:,}")

        # 4. 슬랙으로 현재가 알림 전송
        print("\n[4] Sending price alert to Slack...")
        slack.send_price_alert(kr_price, us_price)

        print("\n" + "=" * 50)
        print("Test completed successfully!")
        print("=" * 50)

    except ValueError as e:
        print(f"\nConfiguration error: {e}")
        slack.send(f"❌ 설정 오류: {e}")
    except Exception as e:
        print(f"\nError: {e}")
        slack.send(f"❌ 오류 발생: {e}")
