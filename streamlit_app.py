"""
미국주식 자동매매 대시보드
실행: streamlit run streamlit_app.py
"""

import os
import streamlit as st
import requests

# ========================================
# 하이브리드 시크릿 관리 (Cloud + Local)
# ========================================
def get_secret(key: str, default: str = None) -> str:
    """Streamlit Cloud 또는 로컬 환경에서 시크릿 가져오기"""
    # 1. Streamlit Cloud secrets 확인
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass

    # 2. 로컬 환경변수 확인 (.env)
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv(key, default)


# ========================================
# KIS API 클래스
# ========================================
class KisAuth:
    """한국투자증권 인증 클래스"""

    BASE_URL = "https://openapi.koreainvestment.com:9443"

    def __init__(self):
        self.app_key = get_secret("KIS_APP_KEY")
        self.app_secret = get_secret("KIS_APP_SECRET")
        self.account_number = get_secret("KIS_ACCOUNT_NUMBER")
        self.account_product_code = get_secret("KIS_ACCOUNT_PRODUCT_CODE", "01")

        self._validate_credentials()
        self.access_token = None

    def _validate_credentials(self):
        missing = []
        if not self.app_key:
            missing.append("KIS_APP_KEY")
        if not self.app_secret:
            missing.append("KIS_APP_SECRET")
        if not self.account_number:
            missing.append("KIS_ACCOUNT_NUMBER")

        if missing:
            raise ValueError(f"Missing secrets: {', '.join(missing)}")

    def get_access_token(self) -> str:
        url = f"{self.BASE_URL}/oauth2/tokenP"
        headers = {"Content-Type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        response = requests.post(url, headers=headers, json=body, timeout=10)
        response.raise_for_status()

        data = response.json()
        self.access_token = data.get("access_token")

        if not self.access_token:
            raise ValueError(f"Token not found: {data}")

        return self.access_token

    def get_auth_headers(self, tr_id: str) -> dict:
        if not self.access_token:
            raise ValueError("Call get_access_token() first")

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
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        tr_id = "HHDFS00000300"

        headers = self.auth.get_auth_headers(tr_id)
        params = {"AUTH": "", "EXCD": exchange, "SYMB": symbol}

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
        }

    def get_balance(self, exchange: str = "NYSE") -> dict:
        """해외주식 주문가능금액 조회"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"
        tr_id = "TTTS3007R"

        headers = self.auth.get_auth_headers(tr_id)
        params = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": exchange,
            "OVRS_ORD_UNPR": "10",
            "ITEM_CD": "F",
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"API error: {data.get('msg1')}")

        output = data.get("output", {})

        # 주문가능금액
        frcr_ord_psbl_amt = float(output.get("frcr_ord_psbl_amt1", 0) or 0)  # 외화 주문가능금액
        exrt = float(output.get("exrt", 0) or 0)  # 환율
        max_qty = int(output.get("ovrs_max_ord_psbl_qty", 0) or 0)  # 최대 주문가능수량
        krw_amt = frcr_ord_psbl_amt * exrt  # 원화 환산

        return {
            "usd_amount": frcr_ord_psbl_amt,
            "krw_amount": krw_amt,
            "exchange_rate": exrt,
            "max_qty": max_qty,
            "raw": data,
        }

    def buy_limit_order(self, symbol: str, quantity: int, price: float, exchange: str = "NYS", is_real: bool = False) -> dict:
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        tr_id = "JTTT1002U"

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
            "ORD_DVSN": "00",
        }

        if not is_real:
            return {
                "success": True,
                "mode": "simulation",
                "order_no": "VIRTUAL_ORDER",
                "message": "가상 주문이 전송되었습니다.",
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
    """Slack 알림 클래스"""

    def __init__(self):
        self.webhook_url = get_secret("SLACK_WEBHOOK_URL")

    def send(self, message: str) -> bool:
        if not self.webhook_url:
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
        except Exception:
            return False


# ========================================
# Streamlit 앱
# ========================================
def main():
    st.set_page_config(
        page_title="My AI Trader",
        page_icon="🚀",
        layout="wide",
    )

    # 세션 상태 초기화
    if "auth" not in st.session_state:
        st.session_state.auth = None
    if "overseas" not in st.session_state:
        st.session_state.overseas = None
    if "current_price" not in st.session_state:
        st.session_state.current_price = None
    if "token_ready" not in st.session_state:
        st.session_state.token_ready = False

    # ========================================
    # 사이드바
    # ========================================
    with st.sidebar:
        st.title("🚀 My AI Trader")
        st.markdown("---")

        # 모드 선택
        trading_mode = st.radio(
            "거래 모드",
            ["🟢 모의 투자 (Simulation)", "🔴 실전 투자 (Real)"],
            index=0,
        )
        is_real_trading = "실전" in trading_mode

        if is_real_trading:
            st.warning("⚠️ 실전 투자 모드입니다!")

        st.markdown("---")

        # 토큰 발급
        if st.button("🔑 API 연결", use_container_width=True):
            try:
                with st.spinner("토큰 발급 중..."):
                    auth = KisAuth()
                    auth.get_access_token()
                    st.session_state.auth = auth
                    st.session_state.overseas = KisOverseas(auth)
                    st.session_state.token_ready = True
                st.success("API 연결 성공!")
            except Exception as e:
                st.error(f"연결 실패: {e}")

        # 잔고 조회
        if st.button("💰 잔고 조회", use_container_width=True):
            if not st.session_state.token_ready:
                st.warning("먼저 API 연결을 해주세요.")
            else:
                try:
                    with st.spinner("잔고 조회 중..."):
                        balance = st.session_state.overseas.get_balance()
                    st.metric("주문가능 (USD)", f"${balance['usd_amount']:.2f}")
                    st.metric("주문가능 (KRW)", f"₩{balance['krw_amount']:,.0f}")
                    st.metric("환율", f"{balance['exchange_rate']:,.2f}")
                    st.caption(f"최대 주문가능: {balance['max_qty']}주")
                except Exception as e:
                    st.error(f"잔고 조회 실패: {e}")

        st.markdown("---")
        st.caption("Made with Streamlit")

    # ========================================
    # 메인 화면
    # ========================================
    st.header("🇺🇸 미국 주식 트레이딩")

    # Step 1: 종목 설정
    st.subheader("Step 1. 종목 설정")
    col1, col2 = st.columns(2)

    with col1:
        symbol = st.text_input("종목 티커", value="F", max_chars=10)
        symbol = symbol.upper()

    with col2:
        exchange = st.selectbox("거래소", ["NYS", "NAS", "AMS"], index=0)

    st.markdown("---")

    # Step 2: 가격 확인
    st.subheader("Step 2. 가격 확인")

    if st.button("🔍 현재가 조회", use_container_width=True):
        if not st.session_state.token_ready:
            st.warning("먼저 사이드바에서 API 연결을 해주세요.")
        else:
            try:
                with st.spinner(f"{symbol} 현재가 조회 중..."):
                    price_info = st.session_state.overseas.get_current_price(symbol, exchange)
                    st.session_state.current_price = price_info

                col1, col2 = st.columns(2)
                with col1:
                    st.metric(
                        label=f"{symbol} 현재가",
                        value=f"${price_info['price']:.2f}",
                        delta=f"{price_info['change_rate']:+.2f}%",
                    )
                with col2:
                    st.info(f"거래소: {exchange}")

            except Exception as e:
                st.error(f"조회 실패: {e}")

    # 현재가 표시 (세션에 저장된 경우)
    if st.session_state.current_price:
        price_info = st.session_state.current_price
        st.success(f"💵 {price_info['symbol']}: ${price_info['price']:.2f} ({price_info['change_rate']:+.2f}%)")

    st.markdown("---")

    # Step 3: 주문 실행
    st.subheader("Step 3. 주문 실행")

    quantity = st.number_input("주문 수량", min_value=1, max_value=100, value=1, step=1)

    # 매수 버튼
    if st.button("⚡ 매수 주문", type="primary", use_container_width=True):
        if not st.session_state.token_ready:
            st.warning("먼저 사이드바에서 API 연결을 해주세요.")
        elif not st.session_state.current_price:
            st.warning("먼저 현재가를 조회해주세요.")
        else:
            st.session_state.show_confirm = True

    # 확인 다이얼로그
    if st.session_state.get("show_confirm"):
        price_info = st.session_state.current_price
        mode_str = "🔴 실전" if is_real_trading else "🟢 모의"

        st.warning(f"""
        **진짜 매수하시겠습니까?**

        - 모드: {mode_str}
        - 종목: {symbol} ({exchange})
        - 수량: {quantity}주
        - 가격: ${price_info['price']:.2f}
        - 예상 금액: ${price_info['price'] * quantity:.2f}
        """)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ 확인", use_container_width=True):
                try:
                    with st.spinner("주문 전송 중..."):
                        result = st.session_state.overseas.buy_limit_order(
                            symbol=symbol,
                            quantity=quantity,
                            price=price_info['price'],
                            exchange=exchange,
                            is_real=is_real_trading,
                        )

                        # 슬랙 알림
                        slack = SlackBot()
                        slack.send(f"{'🔴' if is_real_trading else '🟢'} [{result['mode']}] {symbol} {quantity}주 매수 주문 @ ${price_info['price']:.2f}")

                    if result["success"]:
                        st.success(f"✅ 주문 {'전송' if is_real_trading else '시뮬레이션'} 완료! (주문번호: {result['order_no']})")
                        st.balloons()
                    else:
                        st.error("주문 실패")

                except Exception as e:
                    st.error(f"주문 실패: {e}")

                st.session_state.show_confirm = False
                st.rerun()

        with col2:
            if st.button("❌ 취소", use_container_width=True):
                st.session_state.show_confirm = False
                st.rerun()


if __name__ == "__main__":
    main()
