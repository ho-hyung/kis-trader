"""
자동매매 모니터링 대시보드
실행: streamlit run streamlit_app.py
"""

import os
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone

# ========================================
# 자동매매 대상 종목 (auto_trade.py와 동일)
# ========================================
TARGETS = [
    {"symbol": "VRT", "exchange": "NYS", "name": "Vertiv Holdings", "strategy": "pullback", "tp": 10, "sl": -5},
    {"symbol": "ORCL", "exchange": "NYS", "name": "Oracle", "strategy": "breakout", "tp": 7, "sl": -4},
]

# GitHub 저장소 정보
GITHUB_REPO = "ho-hyung/kis-trader"
GITHUB_WORKFLOW = "trade.yml"

# ========================================
# 환경변수 로드
# ========================================
def get_secret(key: str, default: str = None) -> str:
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv(key, default)


# ========================================
# GitHub Workflow 제어
# ========================================
class GitHubWorkflow:
    def __init__(self):
        self.token = get_secret("GITHUB_TOKEN")
        self.repo = GITHUB_REPO
        self.workflow = GITHUB_WORKFLOW

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    def get_workflow_status(self) -> dict:
        """워크플로우 상태 조회"""
        if not self.token:
            return {"error": "GITHUB_TOKEN이 설정되지 않았습니다"}

        url = f"https://api.github.com/repos/{self.repo}/actions/workflows/{self.workflow}"
        try:
            response = requests.get(url, headers=self._headers(), timeout=10)
            if response.status_code == 200:
                data = response.json()
                return {
                    "state": data.get("state"),  # "active" or "disabled_manually"
                    "name": data.get("name"),
                }
            return {"error": f"API 오류: {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def disable_workflow(self) -> bool:
        """워크플로우 비활성화 (일시정지)"""
        if not self.token:
            return False

        url = f"https://api.github.com/repos/{self.repo}/actions/workflows/{self.workflow}/disable"
        try:
            response = requests.put(url, headers=self._headers(), timeout=10)
            return response.status_code == 204
        except Exception:
            return False

    def enable_workflow(self) -> bool:
        """워크플로우 활성화 (재개)"""
        if not self.token:
            return False

        url = f"https://api.github.com/repos/{self.repo}/actions/workflows/{self.workflow}/enable"
        try:
            response = requests.put(url, headers=self._headers(), timeout=10)
            return response.status_code == 204
        except Exception:
            return False


# ========================================
# KIS API 토큰 캐싱 (1분 제한 우회)
# ========================================
@st.cache_data(ttl=1800, show_spinner=False)  # 30분 캐싱
def get_cached_token(app_key: str, app_secret: str) -> str:
    """토큰을 캐싱하여 API 호출 제한(1분) 우회"""
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }
    response = requests.post(url, json=body, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get("access_token")


# ========================================
# KIS API 클래스
# ========================================
class KisAuth:
    BASE_URL = "https://openapi.koreainvestment.com:9443"

    def __init__(self):
        self.app_key = get_secret("KIS_APP_KEY")
        self.app_secret = get_secret("KIS_APP_SECRET")
        self.account_number = get_secret("KIS_ACCOUNT_NUMBER")
        self.account_product_code = get_secret("KIS_ACCOUNT_PRODUCT_CODE", "01")
        self.access_token = None

    def get_access_token(self) -> str:
        # 캐싱된 토큰 사용
        self.access_token = get_cached_token(self.app_key, self.app_secret)
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
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        headers = self.auth.get_auth_headers("HHDFS00000300")
        params = {"AUTH": "", "EXCD": exchange, "SYMB": symbol}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("rt_cd") != "0":
            return None
        output = data.get("output", {})
        return {
            "price": float(output.get("last", 0) or 0),
            "change": float(output.get("diff", 0) or 0),
            "change_rate": float(output.get("rate", 0) or 0),
            "high": float(output.get("high", 0) or 0),
            "low": float(output.get("low", 0) or 0),
            "volume": int(output.get("tvol", 0) or 0),
        }

    def get_daily_prices(self, symbol: str, exchange: str = "NYS", days: int = 20) -> list:
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/dailyprice"
        headers = self.auth.get_auth_headers("HHDFS76240000")
        params = {
            "AUTH": "", "EXCD": exchange, "SYMB": symbol,
            "GUBN": "0", "BYMD": "", "MODP": "1",
        }
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("rt_cd") != "0":
            return []
        prices = []
        for item in data.get("output2", [])[:days]:
            close = item.get("clos")
            if close:
                prices.append(float(close))
        return prices

    def get_balance(self) -> dict:
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
                    "name": item.get("ovrs_item_name"),
                    "quantity": qty,
                    "avg_price": float(item.get("pchs_avg_pric", 0) or 0),
                    "current_price": float(item.get("now_pric2", 0) or 0),
                    "profit_rate": float(item.get("evlu_pfls_rt", 0) or 0),
                    "profit_amt": float(item.get("frcr_evlu_pfls_amt", 0) or 0),
                })
        return {"holdings": holdings}

    def get_order_amount(self) -> dict:
        """주문가능금액 조회"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"
        headers = self.auth.get_auth_headers("TTTS3007R")
        params = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": "NYSE",
            "OVRS_ORD_UNPR": "10",
            "ITEM_CD": "F",
        }
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        output = data.get("output", {})
        usd = float(output.get("frcr_ord_psbl_amt1", 0) or 0)
        exrt = float(output.get("exrt", 0) or 0)
        return {"usd": usd, "krw": usd * exrt, "exchange_rate": exrt}

    def get_pending_orders(self) -> list:
        """미체결 주문 조회"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-nccs"
        headers = self.auth.get_auth_headers("TTTS3018R")
        params = {
            "CANO": self.auth.account_number,
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "OVRS_EXCG_CD": "NASD",
            "SORT_SQN": "DS",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        orders = []
        for item in data.get("output", []):
            orders.append({
                "order_no": item.get("odno"),
                "symbol": item.get("pdno"),
                "type": "매수" if item.get("sll_buy_dvsn_cd") == "02" else "매도",
                "quantity": int(item.get("ft_ord_qty", 0) or 0),
                "price": float(item.get("ft_ord_unpr3", 0) or 0),
                "time": item.get("ord_tmd"),
            })
        return orders


def calculate_sma(prices: list, period: int = 20) -> float:
    if len(prices) < period:
        return 0
    return sum(prices[:period]) / period


# ========================================
# Streamlit 앱
# ========================================
def get_kst_now():
    """한국 시간 반환"""
    KST = timezone(timedelta(hours=9))
    return datetime.now(KST)


def main():
    st.set_page_config(
        page_title="자동매매 모니터링",
        page_icon="🤖",
        layout="wide",
    )

    now_kst = get_kst_now()

    st.title("🤖 자동매매 모니터링 대시보드")
    st.caption(f"마지막 새로고침: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} (KST)")

    # 새로고침 버튼
    col1, col2, col3 = st.columns([1, 1, 8])
    with col1:
        if st.button("🔄 새로고침", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col2:
        auto_refresh = st.checkbox("자동 새로고침", value=False)

    if auto_refresh:
        st.markdown(
            """
            <meta http-equiv="refresh" content="60">
            """,
            unsafe_allow_html=True,
        )
        st.info("60초마다 자동 새로고침됩니다.")

    st.markdown("---")

    # API 연결
    try:
        auth = KisAuth()
        auth.get_access_token()
        overseas = KisOverseas(auth)
    except Exception as e:
        error_msg = str(e)
        st.error(f"API 연결 실패: {error_msg}")

        # 403 에러인 경우 캐시 클리어 버튼 제공
        if "403" in error_msg:
            st.warning("토큰 발급 제한(1분)에 걸렸을 수 있습니다. 캐시를 초기화하고 다시 시도해보세요.")
            if st.button("🔄 캐시 초기화 후 재시도"):
                get_cached_token.clear()
                st.rerun()
        return

    # ========================================
    # 1. 주문가능금액
    # ========================================
    st.subheader("💰 주문가능금액")
    try:
        amount = overseas.get_order_amount()
        col1, col2, col3 = st.columns(3)
        col1.metric("USD", f"${amount['usd']:.2f}")
        col2.metric("KRW", f"₩{amount['krw']:,.0f}")
        col3.metric("환율", f"{amount['exchange_rate']:,.2f}")
    except Exception as e:
        st.error(f"주문가능금액 조회 실패: {e}")

    st.markdown("---")

    # ========================================
    # 2. 자동매매 대상 종목 현황
    # ========================================
    st.subheader("📊 자동매매 대상 종목")
    st.caption("전략: 현재가 < 20일 이동평균 → 매수")

    cols = st.columns(len(TARGETS))

    for idx, target in enumerate(TARGETS):
        with cols[idx]:
            symbol = target["symbol"]
            exchange = target["exchange"]
            name = target["name"]

            try:
                # 현재가 조회
                price_info = overseas.get_current_price(symbol, exchange)
                if not price_info:
                    st.error(f"{symbol} 조회 실패")
                    continue

                current_price = price_info["price"]
                change_rate = price_info["change_rate"]

                # 20일 이평선
                daily_prices = overseas.get_daily_prices(symbol, exchange, 20)
                sma_20 = calculate_sma(daily_prices, 20)

                # 매수 조건
                buy_signal = current_price < sma_20 if sma_20 > 0 else False

                # 카드 스타일 표시
                if buy_signal:
                    st.success(f"**{symbol}** - {name}")
                    signal_text = "🟢 매수 조건 충족"
                else:
                    st.info(f"**{symbol}** - {name}")
                    signal_text = "⏸️ 대기 중"

                st.metric(
                    label="현재가",
                    value=f"${current_price:.2f}",
                    delta=f"{change_rate:+.2f}%",
                )

                st.caption(f"20일 SMA: ${sma_20:.2f}")

                # 진행 바 (현재가 vs 이평선)
                if sma_20 > 0:
                    ratio = current_price / sma_20
                    st.progress(min(ratio / 1.5, 1.0))
                    st.caption(f"이평선 대비: {(ratio - 1) * 100:+.1f}%")

                st.markdown(f"**{signal_text}**")

            except Exception as e:
                st.error(f"{symbol} 오류: {e}")

    st.markdown("---")

    # ========================================
    # 3. 보유 종목 현황
    # ========================================
    st.subheader("📈 보유 종목")

    try:
        balance = overseas.get_balance()
        holdings = balance["holdings"]

        if holdings:
            for h in holdings:
                col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])

                profit_color = "green" if h["profit_rate"] >= 0 else "red"

                col1.markdown(f"**{h['symbol']}**<br><small>{h['name']}</small>", unsafe_allow_html=True)
                col2.metric("수량", f"{h['quantity']}주")
                col3.metric("평균단가", f"${h['avg_price']:.2f}")
                col4.metric("현재가", f"${h['current_price']:.2f}")
                col5.metric(
                    "손익률",
                    f"{h['profit_rate']:+.2f}%",
                    delta=f"${h['profit_amt']:+.2f}",
                )
                st.markdown("---")
        else:
            st.info("보유 중인 해외주식이 없습니다.")

    except Exception as e:
        st.warning(f"잔고 조회 실패: {e}")

    # ========================================
    # 4. 미체결 주문
    # ========================================
    st.subheader("📋 미체결 주문")

    try:
        pending = overseas.get_pending_orders()
        if pending:
            for order in pending:
                col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                col1.write(f"**{order['symbol']}** ({order['type']})")
                col2.write(f"{order['quantity']}주")
                col3.write(f"${order['price']:.2f}")
                col4.write(f"주문번호: {order['order_no']}")
        else:
            st.info("미체결 주문이 없습니다.")
    except Exception as e:
        st.warning(f"미체결 조회 실패: {e}")

    st.markdown("---")

    # ========================================
    # 5. 자동매매 스케줄 정보
    # ========================================
    st.subheader("⏰ 자동매매 스케줄")

    # 워크플로우 상태 확인 및 제어
    gh = GitHubWorkflow()
    workflow_status = gh.get_workflow_status()

    col1, col2, col3 = st.columns([2, 2, 3])

    with col1:
        st.markdown("""
        **실행 시간 (한국 시간)**
        - 시작: 23:30
        - 종료: 06:00
        - 주기: 30분마다
        - 요일: 평일(월~금)
        """)

    with col2:
        st.markdown("""
        **종목별 전략**
        - VRT: 눌림목 (가격<SMA), +10%/-5%
        - ORCL: 반등 (가격>SMA), +7%/-4%
        """)

    with col3:
        st.markdown("**스케줄 제어**")

        if "error" in workflow_status:
            st.warning(f"상태 조회 불가: {workflow_status['error']}")
            st.caption("GITHUB_TOKEN을 Secrets에 추가하세요")
        else:
            is_active = workflow_status.get("state") == "active"

            if is_active:
                st.success("✅ 자동매매 활성화됨")
                if st.button("⏸️ 일시정지", use_container_width=True):
                    if gh.disable_workflow():
                        st.success("자동매매가 일시정지되었습니다")
                        st.rerun()
                    else:
                        st.error("일시정지 실패")
            else:
                st.error("⏸️ 자동매매 일시정지됨")
                if st.button("▶️ 재개", use_container_width=True):
                    if gh.enable_workflow():
                        st.success("자동매매가 재개되었습니다")
                        st.rerun()
                    else:
                        st.error("재개 실패")

    # 현재 장 상태 (한국 시간 기준)
    hour = now_kst.hour

    if (hour >= 23) or (hour < 6):
        st.info("🟢 미국 장 운영 시간")
    else:
        st.info("🔴 미국 장 마감 시간")

    st.markdown("---")
    st.caption("GitHub Actions로 자동 실행 | Slack 알림 연동")


if __name__ == "__main__":
    main()
