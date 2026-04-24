from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# LINE設定
configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])

# Anthropic設定
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """あなたは株式会社金海興業のLINE公式アカウントのAIアシスタントです。
兵庫県西播磨地域を中心に、生コン・砕石・建設資材の調達・土木工事・残土処理を行っています。

以下の問い合わせに丁寧に対応してください：
- 生コン・砕石・砂・真砂土の価格・在庫確認
- 建設資材の調達依頼
- 残土処理の受け入れ確認
- 土木工事の見積依頼
- 配達スケジュールの確認

価格や在庫の具体的な数字はわからない場合、「担当者より折り返しご連絡いたします」と伝えてください。
返答は簡潔に、丁寧な敬語で行ってください。"""


@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    try:
        handler.handle(body.decode(), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text

    # Claude APIで返答生成
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    reply_text = response.content[0].text

    # LINEに返信
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )


@app.get("/")
def health_check():
    return {"status": "ok", "service": "金海興業 LINE Bot"}
