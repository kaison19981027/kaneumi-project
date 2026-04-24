from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent
from linebot.v3.exceptions import InvalidSignatureError
import anthropic
import os
import asyncio
import requests
from collections import defaultdict, deque
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# 会話履歴（ユーザーIDごと・最大10往復保持）
conversation_histories: dict = defaultdict(lambda: deque(maxlen=20))

# 会社情報（Railway環境変数で設定）
COMPANY_PHONE = os.environ.get("COMPANY_PHONE", "担当者にお電話ください")
COMPANY_HOURS = os.environ.get("COMPANY_HOURS", "平日 8:00〜17:00")
COMPANY_ADDRESS = os.environ.get("COMPANY_ADDRESS", "兵庫県西播磨地域")
LINE_NOTIFY_TOKEN = os.environ.get("LINE_NOTIFY_TOKEN", "")
ADMIN_LINE_USER_ID = os.environ.get("ADMIN_LINE_USER_ID", "")

SYSTEM_PROMPT = f"""あなたは株式会社金海興業のLINE公式アカウントのAIアシスタントです。
兵庫県西播磨地域を中心に、生コン・砕石・建設資材の調達・土木工事・残土処理を行っています。

【会社情報】
電話番号: {COMPANY_PHONE}
営業時間: {COMPANY_HOURS}
所在地: {COMPANY_ADDRESS}

【対応業務】
- 生コンクリート・砕石・砂・真砂土の価格案内・発注受付
- 建設資材の調達手配
- 残土処理の受け入れ相談（種類・数量・場所によって確認が必要）
- 土木工事の見積依頼受付
- 配達スケジュールの確認

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

【試験料金表（2026年度版・税別）】

■ 試し練り
・圧縮/曲げ強度試し練り一式（M-1）: 40,000円
・コンクリート練混ぜ追加（M-3）: 10,000円/40L
・短時間材齢圧縮強度試し練り一式（M-9）: 75,000円

■ 供試体作製
・圧縮強度供試体作製 3本1セット（M-4）: 6,000円
・曲げ強度供試体作製 3本1セット（M-6）: 14,000円
・供試体端面処理（M-5）: 3,000円

■ 工場試験
・スランプ/スランプフロー試験（N-1）: 3,000円/回
・空気量試験（N-2）: 3,000円/回
・圧縮強度試験 3本1セット（N-3）: 4,500円
・曲げ強度試験 3本1セット（N-4）: 10,000円
・単位水量試験（N-13）: 12,000円
・コンクリート中塩化物含有量試験（N-11）: 8,000円

■ 現場代行試験
・圧縮強度試験（Q-1、出荷台数無関係）: 25,000円
・曲げ強度試験（Q-2、出荷台数無関係）: 30,000円
・圧縮強度試験（Q-3、採取車指定）: 35,000円
・曲げ強度試験（Q-4、採取車指定）: 40,000円
・圧縮強度試験（Q-9、工場採取）: 10,000円
・曲げ強度試験（Q-10、工場採取）: 15,000円

■ 外部依頼試験
・圧縮強度試験（S-1）: 12,000円（輸送費込み）
・曲げ強度試験（S-2）: 25,000円（輸送費込み）

■ 試験割増
・時間外（8:00〜17:00以外）: +50%
・休日: +50%
・深夜（22:00〜5:00）: さらに+25%

【応答ルール】
- 数量と単価が揃っている場合は「○m³ × ○○円 = ○○円（税別）」と計算して示すこと
- 「担当者に相談」「発注したい」「電話したい」と言われたら電話番号({COMPANY_PHONE})と営業時間({COMPANY_HOURS})を案内すること
- 砕石・砂・真砂土など価格表にないものは「担当者より折り返しご連絡いたします」と伝えること
- 返答は簡潔に、丁寧な敬語で行うこと
- 回答は200文字以内を目安に簡潔にまとめること"""

QUICK_REPLY = QuickReply(items=[
    QuickReplyItem(action=MessageAction(label="生コン価格", text="普通コンクリートの価格を教えてください")),
    QuickReplyItem(action=MessageAction(label="試験料金", text="試験料金を教えてください")),
    QuickReplyItem(action=MessageAction(label="残土処理", text="残土処理について教えてください")),
    QuickReplyItem(action=MessageAction(label="担当者に相談", text="担当者に相談したいです")),
])

WELCOME_MESSAGE = f"""友だち追加ありがとうございます！
株式会社金海興業のLINE公式アカウントです。

生コン・砕石・建設資材のご注文・お見積もり・残土処理など、お気軽にお問い合わせください。

営業時間: {COMPANY_HOURS}
電話: {COMPANY_PHONE}"""

ESCALATION_KEYWORDS = {"担当者", "発注", "注文したい", "見積もり", "電話", "相談したい", "人と話"}


def notify_admin(user_message: str) -> None:
    if LINE_NOTIFY_TOKEN:
        try:
            requests.post(
                "https://notify-api.line.me/api/notify",
                headers={"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"},
                data={"message": f"\n【問い合わせ通知】\n{user_message}"},
                timeout=5,
            )
            print("[Notify] LINE Notify sent", flush=True)
        except Exception as e:
            print(f"[Notify] Error: {e}", flush=True)

    if ADMIN_LINE_USER_ID:
        try:
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).push_message(PushMessageRequest(
                    to=ADMIN_LINE_USER_ID,
                    messages=[TextMessage(text=f"【問い合わせ通知】\n{user_message}")],
                ))
            print("[Notify] Push sent to admin", flush=True)
        except Exception as e:
            print(f"[Notify] Push error: {e}", flush=True)


@handler.add(FollowEvent)
def handle_follow(event):
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=WELCOME_MESSAGE, quick_reply=QUICK_REPLY)],
            ))
        print("[LINE] Welcome message sent", flush=True)
    except Exception as e:
        print(f"[LINE] Follow error: {e}", flush=True)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        user_id = event.source.user_id
        user_message = event.message.text
        print(f"[LINE] User {user_id[:8]}: {user_message}", flush=True)

        # 担当者通知
        if any(kw in user_message for kw in ESCALATION_KEYWORDS):
            notify_admin(user_message)

        # 会話履歴取得・更新
        history = conversation_histories[user_id]
        history.append({"role": "user", "content": user_message})

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=list(history),
        )
        reply_text = response.content[0].text
        history.append({"role": "assistant", "content": reply_text})

        print(f"[LINE] Replying: {reply_text[:80]}", flush=True)

        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text, quick_reply=QUICK_REPLY)],
            ))
        print("[LINE] Reply sent", flush=True)
    except Exception as e:
        print(f"[LINE] Error: {type(e).__name__}: {e}", flush=True)


@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_text = body.decode()

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, handler.handle, body_text, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        print(f"[LINE] Webhook error: {type(e).__name__}: {e}", flush=True)

    return "OK"


@app.get("/")
def health_check():
    return {"status": "ok", "service": "金海興業 LINE Bot"}
