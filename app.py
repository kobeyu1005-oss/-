import os
import sqlite3
from datetime import datetime
from flask import Flask, abort, request
from google import genai
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# 讀取環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 初始化 Gemini 客戶端
client = genai.Client(api_key=GEMINI_API_KEY)

handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


# 初始化 SQLite 資料庫
def init_db():
  conn = sqlite3.connect("chat_history.db")
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            sender TEXT,
            message TEXT
        )
    """)
  conn.commit()
  conn.close()


init_db()


# 寫入訊息到資料庫
def log_message(sender, message):
  conn = sqlite3.connect("chat_history.db")
  cursor = conn.cursor()
  timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  cursor.execute(
      "INSERT INTO messages (timestamp, sender, message) VALUES (?, ?, ?)",
      (timestamp, sender, message),
  )
  conn.commit()
  conn.close()


# 取得最近的對話紀錄（供 Gemini 參考上下文）
def get_recent_history(limit=10):
  conn = sqlite3.connect("chat_history.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT sender, message FROM messages ORDER BY id DESC LIMIT ?", (limit,)
  )
  rows = cursor.fetchall()
  conn.close()
  return rows[::-1]


# 取得所有紀錄以供摘要
def get_all_history():
  conn = sqlite3.connect("chat_history.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT timestamp, sender, message FROM messages ORDER BY id ASC"
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


@app.route("/callback", methods=["POST"])
def callback():
  signature = request.headers.get("X-Line-Signature", "")
  body = request.get_data(as_text=True)

  try:
    handler.handle(body, signature)
  except InvalidSignatureError:
    abort(400)
  return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
  user_message = event.message.text.strip()

  # 【功能 A：輸入 /summary 取得對話重點摘要】
  if user_message == "/summary":
    history = get_all_history()
    if not history:
      reply_text = "目前還沒有任何聊天紀錄可以摘要喔！"
    else:
      history_text = "\n".join(
          [
              f"[{row[0]}] {'女友' if row[1]=='girlfriend' else 'AI分身'}: {row[2]}"
              for row in history
          ]
      )

      prompt = f"""
以下是與女友的 LINE 聊天紀錄：
{history_text}

請幫我做一個重點摘要，並以條列式回答：
1. 她今天的心情與發生的重點事情
2. 她是否有需要我回覆或處理的事項
3. 建議我等一下主動關心的話題
"""
      try:
        response = client.models.generate_content(
            model="gemini-1.5-flash", contents=prompt
        )
        reply_text = response.text
      except Exception as e:
        print("GEMINI SUMMARY ERROR:", e)  # 印出詳細摘要錯誤訊息
        reply_text = "生成摘要時發生錯誤請稍後再試。"

    with ApiClient(configuration) as api_client:
      line_bot_api = MessagingApi(api_client)
      line_bot_api.reply_message(
          ReplyMessageRequest(
              reply_token=event.reply_token,
              messages=[TextMessage(text=reply_text)],
          )
      )
    return

  # 【功能 B：一般聊天對話，Gemini 代回訊息】
  log_message("girlfriend", user_message)

  recent_history = get_recent_history(limit=10)
  chat_context = "\n".join(
      [
          f"{'女友' if row[0] == 'girlfriend' else '你(AI分身)'}: {row[1]}"
          for row in recent_history
      ]
  )

  system_instruction = (
      "你現在是使用者的分身男友，正在幫忙回覆女友的LINE訊息。"
      "語氣要溫柔貼心自然，帶有日常口語感，適時加上表情符號（如 😊、❤️）。"
      "防呆機制：如果女友問到非常具體、需要本人親自承諾的私密約定或行程（例如「你今晚幾點來載我？」），"
      "請委婉地回答：「寶貝我現在手邊有點忙/在開會，等一下忙完馬上找妳喔～」"
  )

  prompt = f"""
{system_instruction}

最近的對話紀錄：
{chat_context}

請根據以上對話，回覆女友最新的一則訊息："{user_message}"
"""

  try:
    response = client.models.generate_content(
        model="gemini-1.5-flash", contents=prompt
    )
    bot_reply = response.text.strip()
  except Exception as e:
    print("GEMINI CHAT ERROR:", e)  # 印出詳細聊天錯誤訊息
    bot_reply = "寶貝我這邊訊號稍微卡了一下，等我一下喔！"

  log_message("bot", bot_reply)

  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=bot_reply)],
        )
    )


if __name__ == "__main__":
  app.run(port=5000)
