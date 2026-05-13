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
import logging
import requests
from collections import defaultdict, deque
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

conversation_histories: dict = defaultdict(lambda: deque(maxlen=20))
form_states: dict = {}  # user_id -> {"step": int, "data": dict, "type": str}
pending_quotes: dict = {}  # quote_id -> {"customer_id": str, "data": dict, "calc": dict}
_quote_counter = 0

# 普通コンクリート価格マスタ（2026年度版・大阪広域組合単価・税別・円/m³）
CONCRETE_PRICES = {
    18: 34000, 21: 34600, 24: 35200, 27: 35800, 30: 36400,
    33: 37000, 36: 37600, 39: 38300, 40: 38600, 42: 39300, 45: 40000,
}

PROCUREMENT_STEPS = [
    ("material", "どのような資材が必要ですか？\n（例：砕石40号、H鋼 200×200、残土処理など）"),
    ("quantity", "数量を教えてください。\n（例：50m³、10本、トラック5台分など）"),
    ("location", "納入場所の住所を教えてください。\n（都道府県・市区町村まででOKです）"),
    ("deadline", "希望納期を教えてください。\n（例：6月15日、来週中、急ぎなど）"),
    ("contact",  "会社名と担当者名を教えてください。"),
]

QUOTE_STEPS = [
    ("strength", "呼び強度を教えてください。\n（例：21、27、30など）"),
    ("quantity", "数量を教えてください。\n（単位：m³　例：50、120など）"),
    ("location", "現場の場所を教えてください。\n（市区町村まででOKです）"),
    ("deadline", "希望納期を教えてください。\n（例：6月15日、来週中など）"),
    ("contact",  "会社名と担当者名を教えてください。"),
]

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
- 生コンの呼び強度（18・21・24・27・30・33・36・39・40・42・45）の価格は必ず上記の価格表から数字で答えること。「確認します」「折り返します」は不可。
- 「18-18-20N」のような表記は「スランプ18cm・呼び強度18・骨材20mm・普通セメント」の意味。呼び強度部分（2番目の数字）で価格を引くこと。
- 数量と単価が揃っている場合は「○m³ × ○○円 = ○○円（税別）」と計算して示すこと
- 「担当者に相談」「発注したい」「電話したい」と言われたら電話番号({COMPANY_PHONE})と営業時間({COMPANY_HOURS})を案内すること
- 砕石・砂・真砂土など価格表にない品目は「担当者より折り返しご連絡いたします」と伝えること
- マークダウン記法（**太字**、## 見出し、--- 区切り線など）は絶対に使わないこと。LINEでは記号がそのまま表示される。
- 絵文字は使わないこと
- 返答は簡潔に、丁寧な敬語で行うこと
- 回答は150文字以内を目安に簡潔にまとめること"""

QUICK_REPLY = QuickReply(items=[
    QuickReplyItem(action=MessageAction(label="生コン見積もり", text="生コン見積もり")),
    QuickReplyItem(action=MessageAction(label="資材調達依頼", text="資材調達依頼")),
    QuickReplyItem(action=MessageAction(label="生コン価格", text="普通コンクリートの価格を教えてください")),
    QuickReplyItem(action=MessageAction(label="残土処理", text="残土処理について教えてください")),
    QuickReplyItem(action=MessageAction(label="担当者に相談", text="担当者に相談したいです")),
])

WELCOME_MESSAGE = f"""友だち追加ありがとうございます！
株式会社金海興業のLINE公式アカウントです。

生コン・砕石・建設資材のご注文・お見積もり・残土処理など、お気軽にお問い合わせください。

営業時間: {COMPANY_HOURS}
電話: {COMPANY_PHONE}"""

ESCALATION_KEYWORDS = {"発注", "注文したい", "電話", "相談したい", "人と話"}


def generate_quote_id() -> str:
    global _quote_counter
    _quote_counter += 1
    return f"Q{_quote_counter:03d}"


def calculate_quote(strength: int, quantity: float) -> dict | None:
    base = CONCRETE_PRICES.get(strength)
    if not base:
        return None
    discount = 4000 if quantity >= 100 else 2000
    unit_price = base - discount
    total = int(unit_price * quantity)
    return {"base": base, "discount": discount, "unit": unit_price, "qty": quantity, "total": total}


def start_procurement_form(reply_token: str, user_id: str) -> None:
    form_states[user_id] = {"step": 0, "data": {}, "type": "procurement"}
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"資材調達のご依頼ありがとうございます。いくつかお伺いします。\n\n{PROCUREMENT_STEPS[0][1]}")],
        ))
    log.info(f"[Form] Procurement started for {user_id[:8]}")


def start_quote_form(reply_token: str, user_id: str) -> None:
    form_states[user_id] = {"step": 0, "data": {}, "type": "quote"}
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"生コンのお見積もりですね。いくつかお伺いします。\n\n{QUOTE_STEPS[0][1]}")],
        ))
    log.info(f"[Quote] Form started for {user_id[:8]}")


def handle_procurement_step(reply_token: str, user_id: str, user_message: str) -> None:
    state = form_states[user_id]
    step = state["step"]
    key, _ = PROCUREMENT_STEPS[step]
    state["data"][key] = user_message
    state["step"] += 1

    if state["step"] < len(PROCUREMENT_STEPS):
        _, next_question = PROCUREMENT_STEPS[state["step"]]
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=next_question)],
            ))
    else:
        data = state["data"]
        summary = (
            f"以下の内容で承りました。担当者より1〜2営業日以内にご連絡いたします。\n\n"
            f"資材：{data.get('material')}\n"
            f"数量：{data.get('quantity')}\n"
            f"納入場所：{data.get('location')}\n"
            f"希望納期：{data.get('deadline')}\n"
            f"会社名・担当者：{data.get('contact')}"
        )
        notify_admin(
            f"【調達依頼】\n"
            f"資材：{data.get('material')}\n"
            f"数量：{data.get('quantity')}\n"
            f"納入場所：{data.get('location')}\n"
            f"希望納期：{data.get('deadline')}\n"
            f"会社名・担当者：{data.get('contact')}"
        )
        del form_states[user_id]
        log.info(f"[Form] Procurement completed for {user_id[:8]}")
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=summary, quick_reply=QUICK_REPLY)],
            ))


def handle_quote_step(reply_token: str, user_id: str, user_message: str) -> None:
    state = form_states[user_id]
    step = state["step"]
    key, _ = QUOTE_STEPS[step]
    state["data"][key] = user_message
    state["step"] += 1

    if state["step"] < len(QUOTE_STEPS):
        _, next_question = QUOTE_STEPS[state["step"]]
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=next_question)],
            ))
        return

    data = state["data"]
    del form_states[user_id]

    try:
        strength_raw = data.get("strength", "")
        strength = int("".join(filter(str.isdigit, strength_raw)))
        qty_raw = data.get("quantity", "0").replace("m³", "").replace("㎥", "").strip()
        quantity = float("".join(c for c in qty_raw if c.isdigit() or c == "."))
    except (ValueError, TypeError):
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="呼び強度または数量の読み取りができませんでした。担当者より折り返しご連絡いたします。", quick_reply=QUICK_REPLY)],
            ))
        notify_admin(f"【見積もり入力エラー・要確認】\n入力内容：{data}")
        return

    calc = calculate_quote(strength, quantity)
    if not calc:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"呼び強度{strength}は価格表にございません。担当者より折り返しご連絡いたします。", quick_reply=QUICK_REPLY)],
            ))
        notify_admin(f"【見積もり要確認・価格表外】\n呼び強度：{strength}\n詳細：{data}")
        return

    quote_id = generate_quote_id()
    pending_quotes[quote_id] = {"customer_id": user_id, "data": data, "calc": calc}

    admin_msg = (
        f"【見積もり承認依頼】{quote_id}\n"
        f"呼び強度：{strength}\n"
        f"数量：{calc['qty']}m³\n"
        f"単価：{calc['unit']:,}円/m³\n"
        f"合計：{calc['total']:,}円（税別）\n"
        f"現場：{data.get('location')}\n"
        f"納期：{data.get('deadline')}\n"
        f"会社名・担当者：{data.get('contact')}\n\n"
        f"承認→「承認 {quote_id}」\n"
        f"金額修正→「修正 {quote_id} 金額」"
    )
    notify_admin(admin_msg)

    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(
                text=(
                    f"ありがとうございます。担当者が確認後、お見積もりをお送りします。\n\n"
                    f"呼び強度：{strength}\n"
                    f"数量：{calc['qty']}m³\n"
                    f"現場：{data.get('location')}\n\n"
                    f"しばらくお待ちください。"
                ),
                quick_reply=QUICK_REPLY
            )],
        ))
    log.info(f"[Quote] {quote_id} pending approval, customer={user_id[:8]}")


def handle_quote_approval(reply_token: str, quote_id: str, custom_total: int = None) -> None:
    if quote_id not in pending_quotes:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"{quote_id}は見つかりませんでした。")],
            ))
        return

    quote = pending_quotes.pop(quote_id)
    calc = quote["calc"]
    data = quote["data"]
    customer_id = quote["customer_id"]
    total = custom_total if custom_total else calc["total"]

    quote_text = (
        f"お見積もり（{quote_id}）\n"
        f"\n"
        f"品目：普通コンクリート\n"
        f"呼び強度：{data.get('strength')}\n"
        f"数量：{calc['qty']}m³\n"
        f"単価：{calc['unit']:,}円/m³\n"
        f"合計：{total:,}円（税別）\n"
        f"消費税：{int(total * 0.1):,}円\n"
        f"税込合計：{int(total * 1.1):,}円\n"
        f"\n"
        f"現場：{data.get('location')}\n"
        f"希望納期：{data.get('deadline')}\n"
        f"\n"
        f"ご発注・ご不明点はお電話またはLINEにてご連絡ください。\n"
        f"営業時間：{COMPANY_HOURS}\n"
        f"TEL：{COMPANY_PHONE}"
    )

    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).push_message(PushMessageRequest(
                to=customer_id,
                messages=[TextMessage(text=quote_text, quick_reply=QUICK_REPLY)],
            ))
        log.info(f"[Quote] {quote_id} sent to customer {customer_id[:8]}")
    except Exception as e:
        log.error(f"[Quote] Send error: {e}")

    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"{quote_id}の見積もりを送信しました。\n合計：{total:,}円（税別）")],
        ))


def notify_admin(message: str) -> None:
    if LINE_NOTIFY_TOKEN:
        try:
            requests.post(
                "https://notify-api.line.me/api/notify",
                headers={"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"},
                data={"message": f"\n{message}"},
                timeout=5,
            )
            log.info("[Notify] LINE Notify sent")
        except Exception as e:
            log.error(f"[Notify] Error: {e}")

    if ADMIN_LINE_USER_ID:
        try:
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).push_message(PushMessageRequest(
                    to=ADMIN_LINE_USER_ID,
                    messages=[TextMessage(text=message)],
                ))
            log.info("[Notify] Push sent to admin")
        except Exception as e:
            log.error(f"[Notify] Push error: {e}")


@handler.add(FollowEvent)
def handle_follow(event):
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=WELCOME_MESSAGE, quick_reply=QUICK_REPLY)],
            ))
        log.info("[LINE] Welcome message sent")
    except Exception as e:
        log.error(f"[LINE] Follow error: {e}")


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    log.info("[LINE] handle_message called")
    try:
        user_id = event.source.user_id
        user_message = event.message.text
        log.info(f"[LINE] User {user_id[:8]}: {user_message}")

        # 管理者の承認・修正コマンド
        if user_id == ADMIN_LINE_USER_ID:
            parts = user_message.split()
            if parts[0] == "承認" and len(parts) >= 2:
                handle_quote_approval(event.reply_token, parts[1])
                return
            if parts[0] == "修正" and len(parts) >= 3:
                try:
                    custom_total = int("".join(filter(str.isdigit, parts[2])))
                    handle_quote_approval(event.reply_token, parts[1], custom_total)
                except ValueError:
                    with ApiClient(configuration) as api_client:
                        MessagingApi(api_client).reply_message(ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="金額の形式が正しくありません。例：修正 Q001 2800000")],
                        ))
                return

        # フォーム進行中
        if user_id in form_states:
            form_type = form_states[user_id].get("type")
            if form_type == "quote":
                handle_quote_step(event.reply_token, user_id, user_message)
            else:
                handle_procurement_step(event.reply_token, user_id, user_message)
            return

        # 生コン見積もりフォーム起動
        if "生コン見積" in user_message or user_message == "生コン見積もり":
            start_quote_form(event.reply_token, user_id)
            return

        # 調達依頼フォーム起動
        if "調達依頼" in user_message:
            start_procurement_form(event.reply_token, user_id)
            return

        # エスカレーション
        if any(kw in user_message for kw in ESCALATION_KEYWORDS):
            notify_admin(f"【問い合わせ通知】\n{user_message}")

        # AI応答
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

        log.info(f"[LINE] Replying: {reply_text[:80]}")

        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text, quick_reply=QUICK_REPLY)],
            ))
        log.info("[LINE] Reply sent")
    except Exception as e:
        log.error(f"[LINE] Error: {type(e).__name__}: {e}")


@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_text = body.decode()
    log.info(f"[LINE] Webhook received, body={len(body_text)}bytes")

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, handler.handle, body_text, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        log.error(f"[LINE] Webhook error: {type(e).__name__}: {e}")

    return "OK"


@app.get("/")
def health_check():
    return {"status": "ok", "service": "金海興業 LINE Bot"}
