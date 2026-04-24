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
import asyncio
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
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


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_message = event.message.text
        print(f"[LINE] Received: {user_message}", flush=True)

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        reply_text = response.content[0].text
        print(f"[LINE] Replying: {reply_text[:80]}", flush=True)

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        print("[LINE] Reply sent successfully", flush=True)
    except Exception as e:
        print(f"[LINE] Error in handle_message: {type(e).__name__}: {e}", flush=True)


@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_text = body.decode()

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, handler.handle, body_text, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        print(f"[LINE] Webhook error: {type(e).__name__}: {e}", flush=True)

    return "OK"


@app.get("/")
def health_check():
    return {"status": "ok", "service": "金海興業 LINE Bot"}
