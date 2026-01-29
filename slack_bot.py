"""
Slack Webhook 알림 클래스
"""

import os
import requests
from dotenv import load_dotenv


class SlackBot:
    """Slack Webhook을 통한 알림 전송"""

    def __init__(self):
        load_dotenv()
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL")

        if not self.webhook_url:
            print("[SlackBot] Warning: SLACK_WEBHOOK_URL not configured")

    def send(self, message: str) -> bool:
        """
        슬랙으로 메시지 전송

        Args:
            message: 전송할 메시지

        Returns:
            성공 여부
        """
        if not self.webhook_url:
            print("[SlackBot] Webhook URL not configured, skipping...")
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

    def send_price_alert(self, kr_price: dict, us_price: dict) -> bool:
        """
        주식 현재가 알림 전송

        Args:
            kr_price: 국내주식 현재가 정보
            us_price: 해외주식 현재가 정보
        """
        message = (
            f"📊 *주식 현재가 조회 결과*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🇰🇷 {kr_price['name']} ({kr_price['code']}) | {kr_price['current_price']:,}원 ({kr_price['change_rate']:+.2f}%)\n"
            f"🇺🇸 {us_price['name']} ({us_price['code']}) | ${us_price['current_price']:.2f} ({us_price['change_rate']:+.2f}%)"
        )
        return self.send(message)


if __name__ == "__main__":
    bot = SlackBot()
    bot.send("🧪 SlackBot 테스트 메시지입니다.")
