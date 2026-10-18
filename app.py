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


# 取得所有紀錄以供輸出
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

  # 【功能 A：輸入 /all 取得全部聊天記錄】
  if user_message == "/all":
    history = get_all_history()
    if not history:
      reply_text = "目前還沒有任何聊天紀錄喔！"
    else:
      history_lines = [
          f"[{row[0]}] {'女友' if row[1]=='girlfriend' else '我(AI)'}: {row[2]}"
          for row in history
      ]
      reply_text = "\n".join(history_lines)
      if len(reply_text) > 4800:
        reply_text = (
            reply_text[-4850:]
            + "\n...(訊息過長，僅顯示最近的對話記錄)..."
        )

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
          f"{'女友' if row[0] == 'girlfriend' else '你(我)'}: {row[1]}"
          for row in recent_history
      ]
  )

  # 帶入從你截圖中學習的說話風格與慣用語（無表情符號、無胖蜜兒）
  my_style_examples = """
  [真實對話風格與用詞範例]
  女友：你罵我胖胖
  你：那是自動選字啦
  女友：我討厭你
  你：好啦笨蛋 秀秀嘛
  女友：一直好擔心你...我想你
  你：笨笨笨笨 我愛你 我在陽台唱歌 我等等的行程只有摺衣服跟洗澡上班 看你幾點到家
  """

  system_instruction = f"""
你現在是使用者的分身男友，正在幫忙回覆女友的LINE訊息。
你的任務是**完全模仿使用者的聊天風格、語氣、寵溺稱呼（如笨蛋、笨笨）、用詞習慣**。

重要限制規範：
1. **絕對不要使用任何表情符號（Emoji）**。
2. 說話簡短自然、帶有口語感。

以下是使用者平常跟女友對話的真實風格範例：
{my_style_examples}

防呆機制：如果女友問到非常具體、需要本人親自承諾的私密約定或行程（例如「你今晚幾點來載我？」），
請用你的語氣與稱呼委婉地回答：「笨蛋我現在手邊有點忙，等一下忙完馬上找妳喔」
"""

  prompt = f"""
{system_instruction}

最近的對話紀錄：
{chat_context}

請根據以上對話與你的說話風格，回覆女友最新的一則訊息："{user_message}"
"""

  try:
    response = client.models.generate_content(
        model="gemini-3.8-flash", contents=prompt
    )
    bot_reply = response.text.strip()
  except Exception as e:
    print("GEMINI CHAT ERROR:", e)
    bot_reply = "笨蛋我這邊訊號稍微卡了一下，等我一下喔"

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
