from fastapi import FastAPI, Request
import httpx
import os
import json
from datetime import datetime

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")           # 獵頭 + 行程
NOTION_INVESTMENT_DB_ID = os.environ.get("NOTION_INVESTMENT_DB_ID") # 投資記錄
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")                   # Google Gemini（免費）

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

# 投資相關關鍵字
INVESTMENT_KEYWORDS = [
    "買入", "賣出", "轉入", "轉出", "持倉", "加倉", "減倉",
    "BTC", "ETH", "SOL", "USDT", "BNB", "XRP", "DOGE",
    "比特幣", "以太幣", "幣", "加密", "幣安", "Binance",
    "coinbase", "okx", "bybit", "交易所"
]


def is_investment_message(text: str) -> bool:
    text_upper = text.upper()
    return any(kw.upper() in text_upper for kw in INVESTMENT_KEYWORDS)


async def call_gemini(prompt: str) -> str:
    """呼叫 Gemini API"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        result = response.json()
        content = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        # 移除可能的 markdown code block
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return content.strip()


async def parse_investment(text: str) -> dict:
    """解析投資訊息"""
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""你是投資記錄助理，請解析以下加密貨幣交易訊息。
今天是 {today}。

只回傳 JSON，不要其他文字：
{{
  "幣種": "幣的名稱，例如 BTC、ETH、SOL",
  "交易類型": "買入 / 賣出 / 轉入 / 轉出 / 其他",
  "數量": 數字或 null,
  "單價": 數字或 null,
  "貨幣單位": "USD / TWD / USDT 等，無法判斷填 null",
  "備註": "其他補充，沒有就填空字串",
  "日期": "YYYY-MM-DD，無法判斷填今天"
}}

訊息：{text}"""
    content = await call_gemini(prompt)
    return json.loads(content)


async def parse_work_event(text: str) -> dict:
    """解析獵頭 / 行程訊息"""
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""你是獵頭助理，解析老闆傳來的快速記錄訊息。今天是 {today}。

只回傳 JSON，不要其他文字：
{{
  "類型": "行程 / 面試安排 / 候選人更新 / CV送出 / 客戶回饋 / 其他",
  "重點": "一句話總結重點",
  "候選人": "候選人姓名，沒有填空字串",
  "客戶公司": "公司名稱，沒有填空字串",
  "有日期": true 或 false,
  "日期": "YYYY-MM-DD，沒有填空字串",
  "時間": "HH:MM，沒有填空字串"
}}

分類：
- 行程：一般會議、電話、個人事項
- 面試安排：安排面試、確認時間
- 候選人更新：面試結果、意願、狀態
- CV送出：把履歷送給客戶
- 客戶回饋：客戶對候選人的看法
- 其他：無法歸類

訊息：{text}"""
    content = await call_gemini(prompt)
    return json.loads(content)


async def add_investment_to_notion(parsed: dict, original_text: str) -> dict:
    coin = parsed.get("幣種", "未知")
    trade_type = parsed.get("交易類型", "其他")

    properties = {
        "幣種": {"title": [{"text": {"content": f"{trade_type} {coin}"}}]},
        "交易類型": {"select": {"name": trade_type}},
        "日期": {"date": {"start": parsed.get("日期", datetime.now().strftime("%Y-%m-%d"))}},
        "原始訊息": {"rich_text": [{"text": {"content": original_text}}]},
    }
    if parsed.get("數量") is not None:
        properties["數量"] = {"number": float(parsed["數量"])}
    if parsed.get("單價") is not None:
        unit = parsed.get("貨幣單位") or ""
        properties["單價"] = {"rich_text": [{"text": {"content": f"{parsed['單價']} {unit}".strip()}}]}
    if parsed.get("備註"):
        properties["備註"] = {"rich_text": [{"text": {"content": parsed["備註"]}}]}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28",
            },
            json={"parent": {"database_id": NOTION_INVESTMENT_DB_ID}, "properties": properties},
            timeout=30,
        )
        return response.json()


async def add_work_to_notion(parsed: dict, original_text: str) -> dict:
    title = parsed.get("重點") or original_text[:80]

    properties = {
        "內容": {"title": [{"text": {"content": title}}]},
        "類型": {"select": {"name": parsed.get("類型", "其他")}},
        "原始訊息": {"rich_text": [{"text": {"content": original_text}}]},
    }
    if parsed.get("有日期") and parsed.get("日期"):
        date_value = parsed["日期"]
        if parsed.get("時間"):
            date_value = f"{parsed['日期']}T{parsed['時間']}:00+08:00"
        properties["日期"] = {"date": {"start": date_value}}
    if parsed.get("候選人"):
        properties["候選人"] = {"rich_text": [{"text": {"content": parsed["候選人"]}}]}
    if parsed.get("客戶公司"):
        properties["客戶公司"] = {"rich_text": [{"text": {"content": parsed["客戶公司"]}}]}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28",
            },
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties},
            timeout=30,
        )
        return response.json()


async def reply_to_line(reply_token: str, message: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": message}]},
            timeout=30,
        )


TYPE_EMOJI = {
    "行程": "📅", "面試安排": "🤝", "候選人更新": "👤",
    "CV送出": "📄", "客戶回饋": "💬", "其他": "📝",
}


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    for event in body.get("events", []):
        if event["type"] == "message" and event["message"]["type"] == "text":
            user_text = event["message"]["text"]
            reply_token = event["replyToken"]

            try:
                if is_investment_message(user_text):
                    parsed = await parse_investment(user_text)
                    notion_page = await add_investment_to_notion(parsed, user_text)
                    page_url = notion_page.get("url", "")

                    qty = str(parsed["數量"]) if parsed.get("數量") is not None else "－"
                    price = f"{parsed['單價']} {parsed.get('貨幣單位') or ''}".strip() if parsed.get("單價") else "－"

                    reply = (
                        f"💰 投資記錄已存！\n\n"
                        f"🪙 幣種：{parsed.get('幣種', '－')}\n"
                        f"📊 類型：{parsed.get('交易類型', '－')}\n"
                        f"🔢 數量：{qty}\n"
                        f"💵 單價：{price}\n"
                        f"📅 日期：{parsed.get('日期', '－')}"
                    )
                    if parsed.get("備註"):
                        reply += f"\n📝 備註：{parsed['備註']}"
                    if page_url:
                        reply += f"\n\n🔗 {page_url}"

                else:
                    parsed = await parse_work_event(user_text)
                    notion_page = await add_work_to_notion(parsed, user_text)
                    page_url = notion_page.get("url", "")

                    type_name = parsed.get("類型", "其他")
                    emoji = TYPE_EMOJI.get(type_name, "📝")

                    reply = f"✅ 已記錄！\n\n{emoji} 類型：{type_name}\n📌 重點：{parsed.get('重點', user_text)}"
                    if parsed.get("候選人"):
                        reply += f"\n👤 候選人：{parsed['候選人']}"
                    if parsed.get("客戶公司"):
                        reply += f"\n🏢 客戶：{parsed['客戶公司']}"
                    if parsed.get("有日期") and parsed.get("日期"):
                        time_str = f" {parsed['時間']}" if parsed.get("時間") else ""
                        reply += f"\n📅 日期：{parsed['日期']}{time_str}"
                    if page_url:
                        reply += f"\n\n🔗 {page_url}"

            except Exception:
                reply = "⚠️ 記錄失敗，請重試。"

            await reply_to_line(reply_token, reply)

    return {"status": "ok"}


@app.get("/")
async def health():
    return {"status": "running", "message": "LINE Bot 運行中"}
