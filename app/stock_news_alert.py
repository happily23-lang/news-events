"""
📈 주식 호재 뉴스 자동 분석 & 이메일 알림 프로그램
- 매일 경제 뉴스를 크롤링
- Claude AI로 호재/악재 분석 및 관련 종목 매칭
- 결과를 이메일로 전송

필요 라이브러리 설치:
pip install requests beautifulsoup4 anthropic
"""

import requests
from bs4 import BeautifulSoup
import anthropic
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ==============================
# ⚙️ 설정 (여기만 수정하세요!)
# ==============================
CONFIG = {
    # Claude API 키 (https://console.anthropic.com 에서 발급)
    "ANTHROPIC_API_KEY": "여기에_API_키_입력",

    # 이메일 설정 (Gmail 기준)
    "EMAIL_SENDER": "보내는_이메일@gmail.com",
    "EMAIL_PASSWORD": "앱_비밀번호_입력",  # Gmail 앱 비밀번호
    "EMAIL_RECEIVER": "받는_이메일@gmail.com",

    # 분석할 뉴스 수
    "NEWS_COUNT": 30,
}

# ==============================
# 📰 뉴스 크롤링
# ==============================
def crawl_news():
    """네이버 금융 뉴스 크롤링"""
    print("📰 뉴스 크롤링 중...")

    news_list = []

    # 네이버 금융 뉴스 URL들
    urls = [
        "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258",
        "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=259",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = "euc-kr"
            soup = BeautifulSoup(response.text, "html.parser")

            articles = soup.select("dl dd.articleSubject a")

            for article in articles[:CONFIG["NEWS_COUNT"] // len(urls)]:
                title = article.get_text(strip=True)
                href = article.get("href", "")

                # BeautifulSoup이 &sect를 § 로 엔티티 디코딩해서 쿼리가 깨짐.
                # article_id/office_id만 뽑아 모바일 URL로 직접 구성한다.
                import re
                aid = re.search(r"article_id=(\d+)", href)
                oid = re.search(r"office_id=(\d+)", href)
                if not (title and aid and oid):
                    continue
                link = f"https://n.news.naver.com/mnews/article/{oid.group(1)}/{aid.group(1)}"

                news_list.append({
                    "title": title,
                    "link": link,
                    "content": get_article_content(link, headers)
                })

        except Exception as e:
            print(f"⚠️ 크롤링 오류: {e}")

    print(f"✅ {len(news_list)}개 뉴스 수집 완료")
    return news_list


def get_article_content(url, headers):
    """뉴스 본문 내용 가져오기"""
    import re
    try:
        response = requests.get(url, headers=headers, timeout=10)

        # finance.naver.com 레거시 URL은 JS로 n.news.naver.com으로 리다이렉트
        if "finance.naver.com" in url:
            response.encoding = "euc-kr"
            m = re.search(r"top\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", response.text)
            if m:
                response = requests.get(m.group(1), headers=headers, timeout=10)

        # 모바일 네이버는 UTF-8 — requests가 헤더에서 자동 감지하도록 둔다
        soup = BeautifulSoup(response.text, "html.parser")
        content_area = (
            soup.select_one("#dic_area")
            or soup.select_one("#newsct_article")
            or soup.select_one("#content")
            or soup.select_one(".article_body")
        )
        if content_area:
            return content_area.get_text(strip=True)[:500]  # 500자만
    except Exception:
        pass
    return ""


# ==============================
# 🤖 Claude AI 분석
# ==============================
def analyze_with_claude(news_list):
    """Claude AI로 호재 뉴스 및 관련 종목 분석"""
    print("🤖 Claude AI 분석 중...")

    client = anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])

    # 뉴스 목록 텍스트로 변환
    news_text = "\n".join([
        f"{i+1}. 제목: {n['title']}\n   내용: {n['content'][:200]}"
        for i, n in enumerate(news_list)
    ])

    prompt = f"""
오늘의 경제/주식 뉴스를 분석해주세요.

[뉴스 목록]
{news_text}

다음 형식으로 JSON만 출력해주세요 (다른 텍스트 없이):
{{
  "hot_stocks": [
    {{
      "rank": 1,
      "company": "회사명",
      "ticker": "종목코드(있으면)",
      "reason": "호재 이유 (2-3문장)",
      "news_title": "관련 뉴스 제목",
      "sentiment": "강력호재/호재/중립/악재",
      "expected_impact": "단기 주가 영향 예상"
    }}
  ],
  "market_summary": "오늘 시장 전반적인 분위기 요약 (3-4문장)",
  "sector_trends": ["주목할 섹터1", "주목할 섹터2", "주목할 섹터3"],
  "caution": "오늘 주의해야 할 리스크 요인"
}}

호재가 있는 종목만 최대 7개까지 선정해주세요.
"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        result_text = message.content[0].text.strip()

        # JSON 파싱
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        result = json.loads(result_text)
        print(f"✅ AI 분석 완료 - {len(result.get('hot_stocks', []))}개 종목 발굴")
        return result

    except Exception as e:
        print(f"⚠️ AI 분석 오류: {e}")
        return None


# ==============================
# 📧 이메일 전송
# ==============================
def send_email(analysis_result, news_list):
    """분석 결과를 이메일로 전송"""
    print("📧 이메일 전송 중...")

    today = datetime.now().strftime("%Y년 %m월 %d일")

    # 이메일 HTML 작성
    html = build_email_html(analysis_result, today)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 [{today}] 오늘의 주식 호재 종목 알림"
    msg["From"] = CONFIG["EMAIL_SENDER"]
    msg["To"] = CONFIG["EMAIL_RECEIVER"]

    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(CONFIG["EMAIL_SENDER"], CONFIG["EMAIL_PASSWORD"])
            server.sendmail(
                CONFIG["EMAIL_SENDER"],
                CONFIG["EMAIL_RECEIVER"],
                msg.as_bytes()
            )
        print("✅ 이메일 전송 완료!")

    except Exception as e:
        print(f"⚠️ 이메일 전송 오류: {e}")
        print("💡 Gmail 앱 비밀번호 설정 필요: Google 계정 → 보안 → 앱 비밀번호")


def build_email_html(result, today):
    """이메일 HTML 템플릿 생성"""
    if not result:
        return f"<h2>분석 오류가 발생했습니다.</h2>"

    hot_stocks = result.get("hot_stocks", [])
    market_summary = result.get("market_summary", "")
    sector_trends = result.get("sector_trends", [])
    caution = result.get("caution", "")

    # 종목 카드 생성
    stock_cards = ""
    sentiment_colors = {
        "강력호재": "#ff4757",
        "호재": "#ff6b81",
        "중립": "#747d8c",
        "악재": "#2ed573"
    }

    for stock in hot_stocks:
        sentiment = stock.get("sentiment", "중립")
        color = sentiment_colors.get(sentiment, "#747d8c")
        ticker = f"({stock.get('ticker', '')})" if stock.get('ticker') else ""

        stock_cards += f"""
        <div style="background:#f8f9fa; border-left:4px solid {color}; 
                    padding:15px; margin:10px 0; border-radius:5px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h3 style="margin:0; color:#2c3e50;">
                    {stock.get('rank', '')}위. {stock.get('company', '')} {ticker}
                </h3>
                <span style="background:{color}; color:white; padding:3px 10px; 
                             border-radius:20px; font-size:12px;">{sentiment}</span>
            </div>
            <p style="color:#555; margin:8px 0;">📌 {stock.get('reason', '')}</p>
            <p style="color:#888; font-size:13px; margin:5px 0;">
                🗞️ {stock.get('news_title', '')}
            </p>
            <p style="color:#e74c3c; font-size:13px; margin:5px 0;">
                📊 {stock.get('expected_impact', '')}
            </p>
        </div>
        """

    # 섹터 트렌드 태그
    sector_tags = "".join([
        f'<span style="background:#3498db; color:white; padding:4px 10px; '
        f'border-radius:15px; margin:3px; display:inline-block;"># {s}</span>'
        for s in sector_trends
    ])

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: 'Apple SD Gothic Neo', Arial, sans-serif; 
                 max-width:600px; margin:0 auto; background:#fff;">
        
        <!-- 헤더 -->
        <div style="background:linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    padding:30px; text-align:center; border-radius:10px 10px 0 0;">
            <h1 style="color:white; margin:0; font-size:24px;">📈 오늘의 주식 호재 알림</h1>
            <p style="color:rgba(255,255,255,0.8); margin:5px 0;">{today}</p>
        </div>
        
        <!-- 시장 요약 -->
        <div style="background:#fff; padding:20px; border:1px solid #eee;">
            <h2 style="color:#2c3e50; border-bottom:2px solid #667eea; padding-bottom:8px;">
                🌏 오늘의 시장 요약
            </h2>
            <p style="color:#555; line-height:1.8;">{market_summary}</p>
        </div>
        
        <!-- 주목 섹터 -->
        <div style="background:#f8f9fa; padding:20px; border:1px solid #eee;">
            <h2 style="color:#2c3e50; margin-top:0;">🔥 오늘 주목할 섹터</h2>
            <div>{sector_tags}</div>
        </div>
        
        <!-- 호재 종목 -->
        <div style="background:#fff; padding:20px; border:1px solid #eee;">
            <h2 style="color:#2c3e50; border-bottom:2px solid #ff4757; padding-bottom:8px;">
                🚀 오늘의 호재 종목 TOP {len(hot_stocks)}
            </h2>
            {stock_cards}
        </div>
        
        <!-- 주의사항 -->
        <div style="background:#fff3cd; padding:20px; border:1px solid #ffc107; 
                    border-radius:0 0 10px 10px;">
            <h3 style="color:#856404; margin-top:0;">⚠️ 오늘의 리스크 요인</h3>
            <p style="color:#856404; margin:0;">{caution}</p>
        </div>
        
        <!-- 면책 고지 -->
        <div style="padding:15px; text-align:center;">
            <p style="color:#aaa; font-size:11px;">
                ※ 본 알림은 AI가 뉴스를 분석한 참고용 정보입니다.<br>
                투자 결정은 본인의 판단으로 하시고, 투자 손익의 책임은 투자자 본인에게 있습니다.
            </p>
        </div>
        
    </body>
    </html>
    """

    return html


# ==============================
# 🚀 메인 실행
# ==============================
def main():
    print("=" * 50)
    print("📈 주식 호재 뉴스 분석 시작!")
    print(f"⏰ 실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 1. 뉴스 크롤링
    news_list = crawl_news()

    if not news_list:
        print("❌ 뉴스를 가져오지 못했습니다.")
        return

    # 2. Claude AI 분석
    analysis = analyze_with_claude(news_list)

    # 3. 이메일 전송
    send_email(analysis, news_list)

    print("=" * 50)
    print("✅ 모든 작업 완료!")
    print("=" * 50)


if __name__ == "__main__":
    main()