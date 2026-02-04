"""
장 전 뉴스 브리핑
Perplexity API를 활용한 종목별 뉴스 요약
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# 설정
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

# 브리핑 대상 종목
BRIEFING_TARGETS = [
    {"symbol": "VRT", "name": "Vertiv Holdings", "name_kr": "버티브"},
    {"symbol": "ORCL", "name": "Oracle", "name_kr": "오라클"},
    {"symbol": "RKLB", "name": "Rocket Lab", "name_kr": "로켓랩"},
]


def get_news_summary(symbol: str, company_name: str) -> str:
    """Perplexity API로 종목 뉴스 요약 조회"""
    if not PERPLEXITY_API_KEY:
        return "❌ PERPLEXITY_API_KEY 미설정"

    url = "https://api.perplexity.ai/chat/completions"

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = f"""
{symbol} {company_name} 관련 최근 뉴스를 분석해서 다음 형식으로 알려줘:

1. 주요 뉴스 (최대 3개, 한 줄씩)
2. 투자 관점 요약 (호재/악재/중립)
3. 주의할 점 (있다면)

간결하게 한국어로 답변해줘.
"""

    payload = {
        "model": "sonar",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 600
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.status_code == 200:
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content
        else:
            return f"❌ API 오류: {response.status_code}"

    except Exception as e:
        return f"❌ 요청 실패: {e}"


def send_slack(message: str) -> bool:
    """슬랙으로 메시지 전송"""
    if not SLACK_WEBHOOK_URL:
        print(f"[Slack] {message}")
        return False

    try:
        response = requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": message},
            timeout=10,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Slack error: {e}")
        return False


def run_briefing():
    """뉴스 브리핑 실행"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    print("=" * 50)
    print(f"📰 장 전 뉴스 브리핑 시작 ({now})")
    print("=" * 50)

    # 헤더
    briefing_parts = [
        f"📰 *장 전 뉴스 브리핑* ({now})",
        "━" * 30,
    ]

    for target in BRIEFING_TARGETS:
        symbol = target["symbol"]
        name = target["name"]
        name_kr = target["name_kr"]

        print(f"\n{symbol} ({name}) 뉴스 조회 중...")

        summary = get_news_summary(symbol, name)

        print(f"✅ {symbol} 완료")

        # 브리핑에 추가
        briefing_parts.append(f"\n*{symbol} ({name_kr})*")
        briefing_parts.append(summary)
        briefing_parts.append("")

    # 푸터
    briefing_parts.append("━" * 30)
    briefing_parts.append("💡 _Perplexity AI 기반 뉴스 요약_")

    # 전체 메시지
    full_message = "\n".join(briefing_parts)

    print("\n" + "=" * 50)
    print("브리핑 내용:")
    print("=" * 50)
    print(full_message)

    # 슬랙 전송
    print("\n슬랙 전송 중...")
    if send_slack(full_message):
        print("✅ 슬랙 전송 완료")
    else:
        print("⚠️ 슬랙 전송 실패 (또는 미설정)")

    print("\n📰 뉴스 브리핑 완료")


if __name__ == "__main__":
    run_briefing()
