import os
import time
import threading
from datetime import datetime, timezone

import pandas as pd
import pandas_ta as ta
import ccxt

import telebot
from flask import Flask

# =========================
#  CONFIG & SETUP
# =========================

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("TOKEN Telegram belum diset. Tambahkan di Replit Secrets dengan nama 'TOKEN'.")

bot = telebot.TeleBot(TOKEN)

# Chat ID user yang aktif (/start)
USER_CHAT_ID = None

# Web server mini untuk keep-alive di Replit
app = Flask(__name__)

@app.route("/")
def home():
    return "Crypto MACD Signal Bot is running!"

def run_web():
    app.run(host="0.0.0.0", port=8080)

# =========================
#  CRYPTO CONFIG (Binance via ccxt)
# =========================

EXCHANGE_NAME = "Binance"
exchange = ccxt.binance()  # tanpa API key, public market data

# Pair crypto yang akan dipantau
CRYPTO_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
]

# Timeframe Binance yang akan dicek
CRYPTO_TIMEFRAMES = ["5m", "15m", "1h"]  # bisa "1m","5m","15m","1h","4h","1d", dll.

# =========================
#  UTIL
# =========================

def format_time_utc(ts=None):
    if ts is None:
        ts = datetime.now(timezone.utc)
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")

def send_if_chat_set(text: str):
    """Kirim pesan ke chat user kalau USER_CHAT_ID sudah terisi."""
    global USER_CHAT_ID
    if USER_CHAT_ID:
        bot.send_message(USER_CHAT_ID, text, parse_mode="Markdown")

# =========================
#  DATA CRYPTO via CCXT (Binance)
# =========================

def get_ohlcv_ccxt(symbol: str, timeframe: str, limit: int = 200):
    """
    Ambil candlestick crypto dari Binance (ccxt).
    return: list [ [timestamp, open, high, low, close, volume], ... ]
    """
    for attempt in range(2):
        try:
            data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return data
        except ccxt.NetworkError:
            if attempt == 0:
                time.sleep(2)
                continue
            return None
        except ccxt.ExchangeError:
            return None

# =========================
#  ANALISA MACD via pandas-ta
# =========================

def macd_from_ohlc(ohlc):
    if not ohlc or len(ohlc) < 50:
        return None

    df = pd.DataFrame(
        ohlc,
        columns=["time", "open", "high", "low", "close", "volume"]
    )
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)

    macd_df = ta.macd(df["close"])
    if macd_df is None or macd_df.empty:
        return None

    macd_col = "MACD_12_26_9"
    macds_col = "MACDs_12_26_9"
    macdh_col = "MACDh_12_26_9"

    if macd_col not in macd_df.columns:
        return None

    df[macd_col] = macd_df[macd_col]
    df[macds_col] = macd_df[macds_col]
    df[macdh_col] = macd_df[macdh_col]

    last_row = df.iloc[-1]
    return {
        "price": float(last_row["close"]),
        "macd": float(last_row[macd_col]),
        "signal": float(last_row[macds_col]),
        "hist": float(last_row[macdh_col]),
        "time": last_row["time"],
    }

def build_signal_message(symbol, tf, res):
    side = None
    reason = ""

    if res["macd"] > res["signal"] and res["hist"] > 0:
        side = "BUY"
        reason = "MACD Golden Cross, histogram > 0 (bullish momentum)."
    elif res["macd"] < res["signal"] and res["hist"] < 0:
        side = "SELL"
        reason = "MACD Dead Cross, histogram < 0 (bearish momentum)."

    if not side:
        return None, None

    msg = (
        f"🚨 *CRYPTO MACD Signal*\n\n"
        f"Exchange: *{EXCHANGE_NAME}*\n"
        f"Pair: *{symbol}*\n"
        f"Timeframe: *{tf}*\n"
        f"Sinyal: *{side}*\n\n"
        f"Price: `{res['price']:.5f}`\n"
        f"MACD: `{res['macd']:.6f}`\n"
        f"Signal: `{res['signal']:.6f}`\n"
        f"Histogram: `{res['hist']:.6f}`\n"
        f"Waktu candle: {res['time']}\n"
        f"Alasan: {reason}\n"
        f"Update bot: {format_time_utc()}"
    )
    return msg, side

# =========================
#  LOOP SCANNER SINYAL CRYPTO
# =========================

# Untuk menghindari spam, simpan sinyal terakhir (per pair,tf) → "BUY"/"SELL"
LAST_SIGNAL = {}  # key: (symbol,tf) -> "BUY"/"SELL"

def mark_and_should_send(symbol, tf, side):
    key = (symbol, tf)
    last = LAST_SIGNAL.get(key)
    if last == side:
        return False
    LAST_SIGNAL[key] = side
    return True

def crypto_scanner_loop():
    """
    Loop utama: scan semua pair/timeframe,
    hitung MACD, kirim sinyal kalau ada BUY/SELL baru.
    """
    while True:
        try:
            for symbol in CRYPTO_PAIRS:
                for tf in CRYPTO_TIMEFRAMES:
                    try:
                        ohlc = get_ohlcv_ccxt(symbol, timeframe=tf, limit=200)
                        if not ohlc:
                            continue

                        res = macd_from_ohlc(ohlc)
                        if not res:
                            continue

                        msg, side = build_signal_message(symbol, tf, res)
                        if msg and side:
                            if mark_and_should_send(symbol, tf, side):
                                send_if_chat_set(msg)
                    except Exception:
                        # Jangan matikan loop hanya karena 1 error
                        continue

            # jeda antar scan (detik)
            time.sleep(60)
        except Exception:
            time.sleep(60)

# =========================
#  TELEGRAM BOT (sederhana)
# =========================

@bot.message_handler(commands=["start"])
def start_cmd(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    text = (
        "👋 *Crypto MACD Signal Bot*\n\n"
        "Bot ini akan *otomatis* memantau MACD 12,26,9 pada:\n"
        f"Pair: {', '.join(CRYPTO_PAIRS)}\n"
        f"Timeframe: {', '.join(CRYPTO_TIMEFRAMES)}\n\n"
        "Jika ada sinyal MACD:\n"
        "- Golden Cross + histogram > 0 → *BUY*\n"
        "- Dead Cross + histogram < 0 → *SELL*\n\n"
        "Sinyal akan langsung dikirim ke chat ini.\n\n"
        "Ketik `CRYPTO` kapan saja untuk melihat daftar pair & timeframe yang dipantau."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.upper() == "CRYPTO")
def crypto_info(message):
    text = (
        "📊 *CRYPTO yang dipantau:*\n" +
        "\n".join([f"- {p} @ {', '.join(CRYPTO_TIMEFRAMES)}" for p in CRYPTO_PAIRS])
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

# =========================
#  MAIN
# =========================

if __name__ == "__main__":
    print("🤖 Crypto MACD Signal Bot aktif.")
    print("   - CRYPTO only (Binance via ccxt)")
    print("   - Auto scan & kirim sinyal ke Telegram")

    # Jalankan web server (keep-alive)
    t_web = threading.Thread(target=run_web, daemon=True)
    t_web.start()

    # Jalankan scanner crypto
    t_crypto = threading.Thread(target=crypto_scanner_loop, daemon=True)
    t_crypto.start()

    # Jalankan Telegram bot
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
