"""
자동매매 모니터링 대시보드
실행: streamlit run streamlit_app.py
"""

import os
import json
import requests
import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, timedelta, timezone

# 매매 기록 파일
TRADE_HISTORY_FILE = "trade_history.json"

# ========================================
# 자동매매 대상 종목 (auto_trade.py와 동일)
# ========================================
TARGETS = [
    {"symbol": "VRT", "exchange": "NYS", "name": "Vertiv Holdings", "strategy": "pullback", "tp": 10, "sl": -5, "trailing": "+5%→-3%", "extra": "SMA60 체크"},
    {"symbol": "ORCL", "exchange": "NYS", "name": "Oracle", "strategy": "breakout", "tp": 7, "sl": -4, "trailing": "+4%→-2%", "extra": "RSI<70"},
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

    def get_daily_prices(self, symbol: str, exchange: str = "NYS", days: int = 60) -> list:
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

    def get_daily_prices_with_dates(self, symbol: str, exchange: str = "NYS", days: int = 60) -> list:
        """날짜 포함 일봉 데이터"""
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
        result = []
        for item in data.get("output2", [])[:days]:
            close = item.get("clos")
            date_str = item.get("xymd")
            if close and date_str:
                result.append({
                    "date": date_str,
                    "close": float(close),
                    "high": float(item.get("high", close)),
                    "low": float(item.get("low", close)),
                    "volume": int(item.get("tvol", 0) or 0),
                })
        return result

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


def calculate_rsi(prices: list, period: int = 14) -> float:
    """RSI 계산 (0-100)"""
    if len(prices) < period + 1:
        return 50.0

    gains = []
    losses = []
    for i in range(period):
        change = prices[i] - prices[i + 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def load_trade_history() -> list:
    """매매 기록 로드"""
    try:
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []


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
        col1.metric("달러", f"${amount['usd']:.2f}")
        col2.metric("원화", f"₩{amount['krw']:,.0f}")
        col3.metric("환율", f"{amount['exchange_rate']:,.2f}원/$")
    except Exception as e:
        st.error(f"주문가능금액 조회 실패: {e}")

    st.markdown("---")

    # ========================================
    # 2. 자동매매 대상 종목 현황
    # ========================================
    st.subheader("📊 자동매매 대상 종목")

    # 종목별 데이터 저장 (차트용)
    stock_data = {}

    cols = st.columns(len(TARGETS))

    for idx, target in enumerate(TARGETS):
        with cols[idx]:
            symbol = target["symbol"]
            exchange = target["exchange"]
            name = target["name"]
            strategy = target["strategy"]
            tp = target["tp"]
            sl = target["sl"]
            extra = target.get("extra", "")

            try:
                # 현재가 조회
                price_info = overseas.get_current_price(symbol, exchange)
                if not price_info:
                    st.error(f"{symbol} 조회 실패")
                    continue

                current_price = price_info["price"]
                change_rate = price_info["change_rate"]

                # 60일 데이터 (SMA60, 차트용)
                daily_data = overseas.get_daily_prices_with_dates(symbol, exchange, 60)
                daily_prices = [d["close"] for d in daily_data]

                # SMA 계산
                sma_20 = calculate_sma(daily_prices, 20)
                sma_60 = calculate_sma(daily_prices, 60) if len(daily_prices) >= 60 else 0
                rsi = calculate_rsi(daily_prices, 14)

                # 차트 데이터 저장
                stock_data[symbol] = {
                    "daily_data": daily_data,
                    "current_price": current_price,
                    "sma_20": sma_20,
                    "sma_60": sma_60,
                    "rsi": rsi,
                    "strategy": strategy,
                }

                # 전략별 매수 조건 체크
                if strategy == "pullback":
                    # 눌림목: 현재가 < SMA20, SMA60 체크 (상승 추세)
                    buy_signal = current_price < sma_20
                    if "SMA60" in extra and sma_60 > 0:
                        buy_signal = buy_signal and (sma_20 > sma_60)
                    distance_to_signal = ((sma_20 - current_price) / sma_20 * 100) if sma_20 > 0 else 0
                    strategy_desc = "눌림목 전략"
                else:
                    # 반등: 현재가 > SMA20
                    buy_signal = current_price > sma_20
                    if "RSI" in extra:
                        buy_signal = buy_signal and (rsi < 70)
                    distance_to_signal = ((current_price - sma_20) / sma_20 * 100) if sma_20 > 0 else 0
                    strategy_desc = "반등 전략"

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

                # 전략 정보
                st.caption(f"📈 {strategy_desc}")
                st.caption(f"20일 이평선: ${sma_20:.2f}")
                if sma_60 > 0:
                    st.caption(f"60일 이평선: ${sma_60:.2f}")
                st.caption(f"RSI(14): {rsi:.1f}")

                # 매수 시그널까지 거리
                if strategy == "pullback":
                    if distance_to_signal > 0:
                        st.caption(f"📍 20일선까지: {distance_to_signal:.1f}% 아래")
                    else:
                        st.caption(f"🎯 20일선 돌파: {abs(distance_to_signal):.1f}% 위")
                else:
                    if distance_to_signal > 0:
                        st.caption(f"🎯 20일선 돌파: {distance_to_signal:.1f}% 위")
                    else:
                        st.caption(f"📍 20일선까지: {abs(distance_to_signal):.1f}% 아래")

                # 익절/손절/트레일링 라인
                trailing = target.get("trailing", "")
                st.caption(f"🎯 익절: +{tp}% | 🚨 손절: {sl}%")
                if trailing:
                    st.caption(f"📉 트레일링: {trailing}")

                st.markdown(f"**{signal_text}**")

            except Exception as e:
                st.error(f"{symbol} 오류: {e}")

    st.markdown("---")

    # ========================================
    # 2-1. 가격 차트
    # ========================================
    st.subheader("📉 가격 차트 (20일)")

    chart_cols = st.columns(len(TARGETS))
    for idx, target in enumerate(TARGETS):
        symbol = target["symbol"]
        if symbol not in stock_data:
            continue

        with chart_cols[idx]:
            data = stock_data[symbol]
            daily_data = data["daily_data"][:20]  # 최근 20일

            if daily_data:
                # DataFrame 생성
                df = pd.DataFrame(daily_data)
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date")

                # 20일선 추가
                sma_20 = data["sma_20"]
                df["sma20"] = sma_20

                # 가장 가까운 포인트 선택 (넓은 영역)
                nearest = alt.selection_point(
                    nearest=True,
                    on="mouseover",
                    fields=["date"],
                    empty=False
                )

                # 기본 차트
                base = alt.Chart(df).encode(x=alt.X("date:T", title=""))

                # 라인 차트
                line_close = base.mark_line(color="#1f77b4", strokeWidth=2).encode(
                    y=alt.Y("close:Q", title="가격($)")
                )
                line_sma = base.mark_line(color="#ff7f0e", strokeWidth=2, strokeDash=[5, 3]).encode(
                    y=alt.Y("sma20:Q")
                )

                # 투명 선택 영역 (전체 높이) + 툴팁
                selectors = base.mark_rule(strokeWidth=20, opacity=0).encode(
                    tooltip=[
                        alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                        alt.Tooltip("close:Q", title="종가", format="$.2f"),
                        alt.Tooltip("sma20:Q", title="20일선", format="$.2f"),
                    ]
                ).add_params(nearest)

                # 선택된 포인트 표시
                points = base.mark_circle(size=80, color="#1f77b4").encode(
                    y=alt.Y("close:Q"),
                    opacity=alt.condition(nearest, alt.value(1), alt.value(0))
                )

                # 세로선 (선택 위치 표시)
                rules = base.mark_rule(color="gray", strokeDash=[3, 3]).encode(
                    opacity=alt.condition(nearest, alt.value(0.5), alt.value(0))
                ).transform_filter(nearest)

                chart = alt.layer(
                    line_close, line_sma, selectors, points, rules
                ).properties(height=200)

                st.altair_chart(chart, use_container_width=True)
                st.caption(f"{symbol} - 🔵 종가 / 🟠 20일선")

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
                col4.write(f"주문번호 {order['order_no']}")
        else:
            st.info("미체결 주문이 없습니다.")
    except Exception as e:
        st.warning(f"미체결 조회 실패: {e}")

    st.markdown("---")

    # ========================================
    # 5. 매매 기록
    # ========================================
    st.subheader("📜 최근 매매 기록")

    trade_history = load_trade_history()
    if trade_history:
        # 최근 10건만 표시
        recent_trades = trade_history[-10:][::-1]

        for trade in recent_trades:
            action = trade.get("action", "")
            symbol = trade.get("symbol", "")
            price = trade.get("price", 0)
            qty = trade.get("quantity", 0)
            profit_rate = trade.get("profit_rate")
            timestamp = trade.get("timestamp", "")

            if action == "BUY":
                icon = "🟢"
                action_text = "매수"
            elif action == "TAKE_PROFIT":
                icon = "🎉"
                action_text = "익절"
            elif action == "STOP_LOSS":
                icon = "🚨"
                action_text = "손절"
            elif action == "TRAILING_STOP":
                icon = "📉"
                action_text = "트레일링"
            else:
                icon = "⚪"
                action_text = action

            col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
            col1.write(f"{icon} **{symbol}** {action_text}")
            col2.write(f"{qty}주")
            col3.write(f"${price:.2f}" if price else "-")

            if profit_rate is not None:
                profit_color = "green" if profit_rate >= 0 else "red"
                col4.markdown(f"<span style='color:{profit_color}'>{profit_rate:+.2f}%</span> | {timestamp}", unsafe_allow_html=True)
            else:
                col4.write(timestamp)

        st.caption(f"전체 {len(trade_history)}건 중 최근 10건 표시")
    else:
        st.info("아직 매매 기록이 없습니다. 자동매매가 실행되면 여기에 기록됩니다.")

    st.markdown("---")

    # ========================================
    # 6. 자동매매 스케줄 정보
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
        - VRT: 눌림목 + 트레일링(+5%→-3%)
        - ORCL: 반등 + 트레일링(+4%→-2%)
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
    st.caption("깃허브 액션으로 자동 실행 | 슬랙 알림 연동")


if __name__ == "__main__":
    main()
