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

【生コンクリート価格表（2026年度版・税別・単位：円/m³）】
大阪広域生コンクリート協同組合の参考価格です。

■ 普通コンクリート（AE減水剤・高性能AE減水剤）
呼び強度18: 34,000円
呼び強度21: 34,600円
呼び強度24: 35,200円
呼び強度27: 35,800円（SF45: 36,400円）
呼び強度30: 36,400円（SF45: 37,000円）
呼び強度33: 37,000円（SF45: 37,600円、SF50: 38,000円）
呼び強度36: 37,600円（SF45: 38,200円、SF50: 38,600円）
呼び強度39: 38,300円（SF45: 38,900円、SF50: 39,300円）
呼び強度40: 38,600円（SF45: 39,200円、SF50: 39,600円、SF60: 40,000円）
呼び強度42: 39,300円（SF45: 39,900円、SF50: 40,300円、SF55: 40,700円）
呼び強度45: 40,000円（SF45: 40,600円、SF50: 41,000円、SF55: 41,400円）
※スランプ5〜21cmは同価格。SF=スランプフロー(cm)。

■ 舗装コンクリート（AE減水剤）
曲げ強度4.5: スランプ2.5cm→36,400円、6.5cm→37,400円
曲げ強度5.0: スランプ2.5cm→37,300円、6.5cm→38,600円

■ 高強度コンクリート（保証材齢28日）
呼び強度46〜50: 42,500円（低熱セメント: 46,900円）
呼び強度51〜55: 43,400円（低熱セメント: 48,100円）
呼び強度56〜60: 44,900円（低熱セメント: 49,600円）

■ モルタル（普通・高炉B種セメント）
セメント400kgまで: 39,700円
セメント550kgまで: 41,700円
セメント700kgまで: 44,200円

■ 透水コンクリート（工場渡し）
豆砂利: 36,700円　／　7号砕石: 39,800円

■ 主な割増料金
・早強セメント（呼び強度30未満）: +1,200円
・早強セメント（呼び強度30〜36）: +1,400円
・高炉セメントB種（保証材齢56日）: ▲250円
・規定時間外（17:30〜20:00打設完了）: +2,000円/m³
・土曜日出荷: +1,000円/m³
・休日出荷: 定額200,000円 +1,000円/m³
・4t・5t車指定: +4,000円/m³
・工場渡し: ▲1,500円/m³

価格は税別です。詳しい数量や配合のご相談は「担当者より折り返しご連絡いたします」とお伝えください。
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
