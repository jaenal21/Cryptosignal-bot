import os
import time
import threading
from datetime import datetime, timezone

import pandas as pd
import pandas_ta as ta
import ccxt
import matplotlib
matplotlib.use("Agg")  # backend non-GUI untuk server
import matplotlib.pyplot as plt

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

# Pair crypto yang akan dipantau auto-signal
CRYPTO_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "PAXG/USDT",
    "XRP/USDT",
    "DOT/USDT",
]

# Timeframe auto-signal
CRYPTO_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]

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
#  LOOP SCANNER SINYAL CRYPTO (AUTO)
# =========================

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
                        continue

            time.sleep(60)
        except Exception:
            time.sleep(60)

# =========================
#  FITUR CHART /tf
# =========================

def plot_chart_with_macd(symbol: str, timeframe: str, limit: int = 200):
    """
    Ambil data dari Binance dan buat chart candlestick simple + garis MACD & Signal.
    Return path file png.
    """
    ohlc = get_ohlcv_ccxt(symbol, timeframe, limit=limit)
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

    # Mulai plotting
    plt.figure(figsize=(10, 6))

    # Subplot 1: harga (close) aja biar simple (bisa dikembangkan ke candlestick utuh)
    ax1 = plt.subplot(2, 1, 1)
    ax1.plot(df["time"], df["close"])
    ax1.set_title(f"{symbol} - {timeframe} Price")
    ax1.set_ylabel("Price")

    # Subplot 2: MACD & Signal + Histogram
    ax2 = plt.subplot(2, 1, 2)
    ax2.plot(df["time"], df[macd_col], label="MACD")
    ax2.plot(df["time"], df[macds_col], label="Signal")
    ax2.bar(df["time"], df[macdh_col], width=0.01, label="Hist")  # width kecil biar nggak dempet
    ax2.set_title("MACD 12,26,9")
    ax2.legend(loc="best")

    plt.tight_layout()

    # Simpan ke file
    filename = f"chart_{symbol.replace('/', '')}_{timeframe}.png"
    plt.savefig(filename)
    plt.close()
    return filename

# =========================
#  TELEGRAM BOT
# =========================

@bot.message_handler(commands=["start"])
def start_cmd(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    text = (
        "👋 *Crypto MACD Signal Bot*\n\n"
        "Bot ini:\n"
        "1️⃣ *Auto-signal MACD* 12,26,9 untuk:\n"
        f"   Pair: {', '.join(CRYPTO_PAIRS)}\n"
        f"   TF  : {', '.join(CRYPTO_TIMEFRAMES)}\n\n"
        "2️⃣ *Fitur chart manual* via command:\n"
        "   `/tf <timeframe> <symbol>`\n"
        "   Contoh:\n"
        "   • `/tf 1h BTCUSDT`\n"
        "   • `/tf 4h ETHUSDT`\n"
        "   • `/tf 30m XRPUSDT`\n"
        "   • `/tf 1d PAXGUSDT`\n\n"
        "Timeframe yang didukung mengikuti Binance / ccxt:\n"
        "`1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M`\n\n"
        "Sinyal BUY/SELL akan otomatis dikirim ke chat ini."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.upper() == "CRYPTO")
def crypto_info(message):
    text = (
        "📊 *CRYPTO yang dipantau auto-signal:*\n" +
        "\n".join([f"- {p} @ {', '.join(CRYPTO_TIMEFRAMES)}" for p in CRYPTO_PAIRS])
        + "\n\nUntuk chart manual, gunakan: `/tf <tf> <symbol>`"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=["tf"])
def tf_chart_handler(message):
    """
    Command: /tf <timeframe> <symbol>
    Contoh: /tf 1h BTCUSDT
            /tf 30m ETHUSDT
    """
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(
                message,
                "Format salah.\nContoh yang benar:\n`/tf 1h BTCUSDT`\n`/tf 30m BTCUSDT`",
                parse_mode="Markdown",
            )
            return

        tf = parts[1].strip()
        symbol_raw = parts[2].strip().upper()

        # Ubah BTCUSDT → BTC/USDT
        if symbol_raw.endswith("USDT"):
            base = symbol_raw.replace("USDT", "")
            symbol = f"{base}/USDT"
        else:
            # fallback: kalau user sudah ketik BTC/USDT
            symbol = symbol_raw

        bot.reply_to(message, f"⏳ Mengambil chart {symbol} timeframe {tf} dari {EXCHANGE_NAME}...")

        file_path = plot_chart_with_macd(symbol, tf)
        if not file_path:
            bot.reply_to(message, "Gagal membuat chart. Coba timeframe lain atau cek simbolnya lagi.")
            return

        with open(file_path, "rb") as photo:
            caption = f"{symbol} - {tf} (Price + MACD)"
            bot.send_photo(message.chat.id, photo, caption=caption)
    except Exception as e:
        bot.reply_to(message, f"Terjadi error saat membuat chart: `{e}`", parse_mode="Markdown")

# =========================
#  MAIN
# =========================

if __name__ == "__main__":
    print("🤖 Crypto MACD Signal Bot aktif.")
    print("   - CRYPTO only (Binance via ccxt)")
    print("   - Auto scan & kirim sinyal ke Telegram")
    print("   - Pair:", ", ".join(CRYPTO_PAIRS))
    print("   - TF:", ", ".join(CRYPTO_TIMEFRAMES))
    print("   - Fitur /tf untuk chart manual")

    # Jalankan web server (keep-alive)
    t_web = threading.Thread(target=run_web, daemon=True)
    t_web.start()

    # Jalankan scanner crypto
    t_crypto = threading.Thread(target=crypto_scanner_loop, daemon=True)
    t_crypto.start()

    # Jalankan Telegram bot
    bot.infinity_polling(timeout=60, long_polling_timeout=30)    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ====================== BINANCE ======================
exchange = ccxt.binance({
    'enableRateLimit': True,
    'timeout': 30000,
})

PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "PAXG/USDT", "DOT/USDT"]
TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]

LAST_SIGNAL = {}   # (symbol, tf) -> "BUY"/"SELL"

# ====================== UTIL ======================
def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def send(text):
    global USER_CHAT_ID
    if USER_CHAT_ID:
        try:
            bot.send_message(USER_CHAT_ID, text, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Gagal kirim Telegram: {e}")

# ====================== MACD CROSS DETECTION ======================
def check_macd_cross(symbol, tf):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=100)
        if len(ohlcv) < 50:
            return None

        df = pd.DataFrame(ohlcv, columns=["ts", "o", "h", "l", "c", "v"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        macd = ta.macd(df["c"])
        if macd is None or macd.empty:
            return None

        df["macd"] = macd["MACD_12_26_9"]
        df["signal"] = macd["MACDs_12_26_9"]
        df["hist"] = macd["MACDh_12_26_9"]

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        key = (symbol, tf)
        last = LAST_SIGNAL.get(key)

        # Golden Cross
        if prev["macd"] <= prev["signal"] and curr["macd"] > curr["signal"]:
            if last != "BUY":
                LAST_SIGNAL[key] = "BUY"
                msg = (
                    f"*MACD GOLDEN CROSS*\n\n"
                    f"*Pair:* `{symbol}`\n"
                    f"*TF:* `{tf}`\n"
                    f"*Signal:* BUY\n\n"
                    f"Price: `{curr['c']:.6f}`\n"
                    f"MACD: `{curr['macd']:.6f}`\n"
                    f"Signal Line: `{curr['signal']:.6f}`\n"
                    f"Histogram: `{curr['hist']:.6f}`\n"
                    f"Time: `{curr['date'].strftime('%Y-%m-%d %H:%M')} UTC`"
                )
                return msg

        # Death Cross
        elif prev["macd"] >= prev["signal"] and curr["macd"] < curr["signal"]:
            if last != "SELL":
                LAST_SIGNAL[key] = "SELL"
                msg = (
                    f"*MACD DEATH CROSS*\n\n"
                    f"*Pair:* `{symbol}`\n"
                    f"*TF:* `{tf}`\n"
                    f"*Signal:* SELL\n\n"
                    f"Price: `{curr['c']:.6f}`\n"
                    f"MACD: `{curr['macd']:.6f}`\n"
                    f"Signal Line: `{curr['signal']:.6f}`\n"
                    f"Histogram: `{curr['hist']:.6f}`\n"
                    f"Time: `{curr['date'].strftime('%Y-%m-%d %H:%M')} UTC`"
                )
                return msg
    except Exception as e:
        logger.warning(f"Error {symbol} {tf}: {e}")
    return None

# ====================== SCANNER LOOP ======================
def scanner():
    logger.info("Scanner started")
    while True:
        try:
            for symbol in PAIRS:
                for tf in TIMEFRAMES:
                    msg = check_macd_cross(symbol, tf)
                    if msg:
                        send(msg)
                    time.sleep(0.7)
            logger.info(f"Scan complete – {utc_now()}")
            time.sleep(45)
        except Exception as e:
            logger.error(f"Scanner crash: {e}")
            time.sleep(60)

# ====================== CHART /tf ======================
def make_chart(symbol, tf):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=150)
        df = pd.DataFrame(ohlcv, columns=["ts","o","h","l","c","v"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        macd = ta.macd(df["c"])
        df["macd"] = macd["MACD_12_26_9"]
        df["signal"] = macd["MACDs_12_26_9"]
        df["hist"] = macd["MACDh_12_26_9"]

        plt.figure(figsize=(12,8))
        plt.subplot(2,1,1)
        plt.plot(df["date"], df["c"], color="#8b5cf6")
        plt.title(f"{symbol} {tf} – Price")
        plt.grid(alpha=0.3)

        plt.subplot(2,1,2)
        plt.plot(df["date"], df["macd"], label="MACD", color="blue")
        plt.plot(df["date"], df["signal"], label="Signal", color="orange")
        plt.bar(df["date"], df["hist"], label="Histogram", color="gray", alpha=0.6, width=0.001)
        plt.axhline(0, color="white", linewidth=0.8, linestyle="--")
        plt.legend()
        plt.title("MACD (12,26,9)")
        plt.grid(alpha=0.3)

        plt.tight_layout()
        path = f"/tmp/chart_{symbol.replace('/', '')}_{tf}.png"
        plt.savefig(path, dpi=200)
        plt.close()
        return path
    except:
        return None

# ====================== TELEGRAM HANDLERS ======================
@bot.message_handler(commands=["start"])
def start(msg):
    global USER_CHAT_ID
    USER_CHAT_ID = msg.chat.id
    text = (
        "*Crypto MACD Cross Signal Bot*\n\n"
        "Bot akan kirim notif hanya saat terjadi:\n"
        "• Golden Cross → BUY\n"
        "• Death Cross → SELL\n\n"
        f"Pair: {', '.join(PAIRS)}\n"
        f"TF: {', '.join(TIMEFRAMES)}\n\n"
        "Chart manual: `/tf 1h BTCUSDT`"
    )
    bot.send_message(msg.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=["tf"])
def chart(msg):
    try:
        parts = msg.text.split()
        if len(parts) != 3:
            bot.reply_to(msg, "Gunakan: `/tf 1h BTCUSDT`", parse_mode="Markdown")
            return
        tf = parts[1].lower()
        sym = parts[2].upper().replace("USDT", "/USDT")
        bot.reply_to(msg, f"Mengambil {sym} {tf}...")
        path = make_chart(sym, tf)
        if path and os.path.exists(path):
            with open(path, "rb") as p:
                bot.send_photo(msg.chat.id, p, caption=f"{sym} – {tf}")
            os.remove(path)
        else:
            bot.reply_to(msg, "Gagal buat chart.")
    except Exception as e:
        bot.reply_to(msg, f"Error: {e}")

# ====================== MAIN ======================
if __name__ == "__main__":
    logger.info("Bot starting...")
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
    bot.infinity_polling(none_stop=True, interval=0, timeout=60)
bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Chat ID user yang aktif (bisa banyak, simpan sebagai set)
ACTIVE_CHAT_IDS = set()

# =========================
#  CRYPTO CONFIG
# =========================

EXCHANGE_NAME = "Binance"
exchange = ccxt.binance()

CRYPTO_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "PAXG/USDT",
    "XRP/USDT",
    "DOT/USDT",
]

CRYPTO_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]

# Simpan sinyal terakhir: (symbol, tf) -> "BUY"/"SELL"
LAST_SIGNAL = {}


# =========================
#  UTIL
# =========================

def format_time_utc(ts=None):
    if ts is None:
        ts = datetime.now(timezone.utc)
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


def send_to_all_active(text: str):
    for chat_id in list(ACTIVE_CHAT_IDS):
        try:
            bot.send_message(chat_id, text, parse_mode="Markdown")
        except Exception:
            # kalau error (user blokir bot, dll) – hapus dari list
            ACTIVE_CHAT_IDS.discard(chat_id)


# =========================
#  DATA CRYPTO / MACD
# =========================

def get_ohlcv_ccxt(symbol: str, timeframe: str, limit: int = 200):
    for attempt in range(2):
        try:
            return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except ccxt.NetworkError:
            if attempt == 0:
                time.sleep(2)
                continue
            return None
        except ccxt.ExchangeError:
            return None


def macd_from_ohlc(ohlc):
    if not ohlc or len(ohlc) < 50:
        return None

    df = pd.DataFrame(
        ohlc,
        columns=["time", "open", "high", "low", "close", "volume"],
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

    last = df.iloc[-1]
    return {
        "price": float(last["close"]),
        "macd": float(last[macd_col]),
        "signal": float(last[macds_col]),
        "hist": float(last[macdh_col]),
        "time": last["time"],
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
#  SCANNER AUTO-SIGNAL
# =========================

def mark_and_should_send(symbol, tf, side):
    key = (symbol, tf)
    last = LAST_SIGNAL.get(key)
    if last == side:
        return False
    LAST_SIGNAL[key] = side
    return True


def crypto_scanner_loop():
    while True:
        try:
            for symbol in CRYPTO_PAIRS:
                for tf in CRYPTO_TIMEFRAMES:
                    try:
                        ohlc = get_ohlcv_ccxt(symbol, tf, limit=200)
                        if not ohlc:
                            continue

                        res = macd_from_ohlc(ohlc)
                        if not res:
                            continue

                        msg, side = build_signal_message(symbol, tf, res)
                        if msg and side and mark_and_should_send(symbol, tf, side):
                            send_to_all_active(msg)
                    except Exception:
                        continue

            time.sleep(60)
        except Exception:
            time.sleep(60)


# =========================
#  CHART GENERATOR
# =========================

def plot_chart_with_macd(symbol: str, timeframe: str, limit: int = 200):
    ohlc = get_ohlcv_ccxt(symbol, timeframe, limit=limit)
    if not ohlc or len(ohlc) < 50:
        return None

    df = pd.DataFrame(
        ohlc,
        columns=["time", "open", "high", "low", "close", "volume"],
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

    plt.figure(figsize=(10, 6))

    ax1 = plt.subplot(2, 1, 1)
    ax1.plot(df["time"], df["close"])
    ax1.set_title(f"{symbol} - {timeframe} Price")
    ax1.set_ylabel("Price")

    ax2 = plt.subplot(2, 1, 2)
    ax2.plot(df["time"], df[macd_col], label="MACD")
    ax2.plot(df["time"], df[macds_col], label="Signal")
    ax2.bar(df["time"], df[macdh_col], width=0.01, label="Hist")
    ax2.set_title("MACD 12,26,9")
    ax2.legend(loc="best")

    plt.tight_layout()

    filename = f"chart_{symbol.replace('/', '')}_{timeframe}.png"
    plt.savefig(filename)
    plt.close()

    return filename


# =========================
#  TELEGRAM HANDLERS
# =========================

@bot.message_handler(commands=["start"])
def start_cmd(message):
    chat_id = message.chat.id
    ACTIVE_CHAT_IDS.add(chat_id)

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(types.KeyboardButton("CRYPTO"), types.KeyboardButton("Chart"))

    text = (
        "👋 *Crypto MACD Signal Bot*\n\n"
        "Bot ini:\n"
        "1️⃣ *Auto-signal MACD* 12,26,9 untuk:\n"
        f"   Pair: {', '.join(CRYPTO_PAIRS)}\n"
        f"   TF  : {', '.join(CRYPTO_TIMEFRAMES)}\n\n"
        "2️⃣ *Fitur chart cepat* via tombol *Chart*:\n"
        "   - Tekan tombol `Chart`\n"
        "   - Lalu ketik: `BTCUSDT 1h` atau `ETHUSDT 4h`\n\n"
        "Timeframe yang didukung (Binance/ccxt):\n"
        "`1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M`\n\n"
        "Sinyal BUY/SELL akan otomatis dikirim ke chat ini."
    )
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text and m.text.upper() == "CRYPTO")
def crypto_info(message):
    text = (
        "📊 *CRYPTO yang dipantau auto-signal:*\n"
        + "\n".join([f"- {p} @ {', '.join(CRYPTO_TIMEFRAMES)}" for p in CRYPTO_PAIRS])
        + "\n\nUntuk chart, tekan tombol *Chart* lalu ketik: `BTCUSDT 1h`."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower() == "chart")
def chart_menu(message):
    text = (
        "🧾 *Mode Chart Manual*\n\n"
        "Silakan ketik pair + timeframe dengan format:\n"
        "`BTCUSDT 1h`\n"
        "`ETHUSDT 4h`\n"
        "`XRPUSDT 30m`\n"
        "`PAXGUSDT 1d`\n\n"
        "Aturan:\n"
        "- Symbol: pakai format Binance (BTCUSDT, ETHUSDT, XRPUSDT, DOTUSDT, dll)\n"
        "- Timeframe: 1m,5m,15m,30m,1h,4h,1d, dll.\n"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def generic_text_handler(message):
    text = message.text.strip().upper()

    if text in ["CRYPTO", "CHART", "/START"]:
        return

    parts = text.split()
    if len(parts) != 2:
        return

    symbol_raw, tf = parts[0], parts[1]

    if symbol_raw.endswith("USDT"):
        base = symbol_raw.replace("USDT", "")
        symbol = f"{base}/USDT"
    else:
        symbol = symbol_raw

    try:
        bot.reply_to(message, f"⏳ Mengambil chart {symbol} timeframe {tf} dari {EXCHANGE_NAME}...")

        file_path = plot_chart_with_macd(symbol, tf)
        if not file_path:
            bot.reply_to(
                message,
                "Gagal membuat chart. Coba cek lagi symbol/timeframenya.\n"
                "Contoh: `BTCUSDT 1h`",
                parse_mode="Markdown",
            )
            return

        with open(file_path, "rb") as photo:
            caption = f"{symbol} - {tf} (Price + MACD)"
            bot.send_photo(message.chat.id, photo, caption=caption)
    except Exception as e:
        bot.reply_to(message, f"Error saat membuat chart: `{e}`", parse_mode="Markdown")


# =========================
#  FLASK + WEBHOOK
# =========================

@app.route("/", methods=["GET"])
def index():
    return "Crypto MACD Signal Bot - OK"


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "OK", 200
    else:
        return "Unsupported Media Type", 415


def set_webhook():
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=WEBHOOK_URL)


# =========================
#  MAIN
# =========================

if __name__ == "__main__":
    print("Starting Crypto MACD Bot (Render/webhook mode)...")
    print("Webhook URL:", WEBHOOK_URL)

    # set webhook Telegram
    set_webhook()

    # start scanner auto-signal di thread terpisah
    t_scan = threading.Thread(target=crypto_scanner_loop, daemon=True)
    t_scan.start()

    # jalankan Flask (Render akan call gunicorn / python main.py)
    app.run(host="0.0.0.0", port=PORT)    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "PAXG/USDT",
    "XRP/USDT",
    "DOT/USDT",
]

# Timeframe auto-signal
CRYPTO_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]

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
#  LOOP SCANNER SINYAL CRYPTO (AUTO)
# =========================

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
                        continue

            time.sleep(60)
        except Exception:
            time.sleep(60)

# =========================
#  FITUR CHART /tf
# =========================

def plot_chart_with_macd(symbol: str, timeframe: str, limit: int = 200):
    """
    Ambil data dari Binance dan buat chart candlestick simple + garis MACD & Signal.
    Return path file png.
    """
    ohlc = get_ohlcv_ccxt(symbol, timeframe, limit=limit)
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

    # Mulai plotting
    plt.figure(figsize=(10, 6))

    # Subplot 1: harga (close) aja biar simple (bisa dikembangkan ke candlestick utuh)
    ax1 = plt.subplot(2, 1, 1)
    ax1.plot(df["time"], df["close"])
    ax1.set_title(f"{symbol} - {timeframe} Price")
    ax1.set_ylabel("Price")

    # Subplot 2: MACD & Signal + Histogram
    ax2 = plt.subplot(2, 1, 2)
    ax2.plot(df["time"], df[macd_col], label="MACD")
    ax2.plot(df["time"], df[macds_col], label="Signal")
    ax2.bar(df["time"], df[macdh_col], width=0.01, label="Hist")  # width kecil biar nggak dempet
    ax2.set_title("MACD 12,26,9")
    ax2.legend(loc="best")

    plt.tight_layout()

    # Simpan ke file
    filename = f"chart_{symbol.replace('/', '')}_{timeframe}.png"
    plt.savefig(filename)
    plt.close()
    return filename

# =========================
#  TELEGRAM BOT
# =========================

@bot.message_handler(commands=["start"])
def start_cmd(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id
    text = (
        "👋 *Crypto MACD Signal Bot*\n\n"
        "Bot ini:\n"
        "1️⃣ *Auto-signal MACD* 12,26,9 untuk:\n"
        f"   Pair: {', '.join(CRYPTO_PAIRS)}\n"
        f"   TF  : {', '.join(CRYPTO_TIMEFRAMES)}\n\n"
        "2️⃣ *Fitur chart manual* via command:\n"
        "   `/tf <timeframe> <symbol>`\n"
        "   Contoh:\n"
        "   • `/tf 1h BTCUSDT`\n"
        "   • `/tf 4h ETHUSDT`\n"
        "   • `/tf 30m XRPUSDT`\n"
        "   • `/tf 1d PAXGUSDT`\n\n"
        "Timeframe yang didukung mengikuti Binance / ccxt:\n"
        "`1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M`\n\n"
        "Sinyal BUY/SELL akan otomatis dikirim ke chat ini."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.upper() == "CRYPTO")
def crypto_info(message):
    text = (
        "📊 *CRYPTO yang dipantau auto-signal:*\n" +
        "\n".join([f"- {p} @ {', '.join(CRYPTO_TIMEFRAMES)}" for p in CRYPTO_PAIRS])
        + "\n\nUntuk chart manual, gunakan: `/tf <tf> <symbol>`"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=["tf"])
def tf_chart_handler(message):
    """
    Command: /tf <timeframe> <symbol>
    Contoh: /tf 1h BTCUSDT
            /tf 30m ETHUSDT
    """
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(
                message,
                "Format salah.\nContoh yang benar:\n`/tf 1h BTCUSDT`\n`/tf 30m BTCUSDT`",
                parse_mode="Markdown",
            )
            return

        tf = parts[1].strip()
        symbol_raw = parts[2].strip().upper()

        # Ubah BTCUSDT → BTC/USDT
        if symbol_raw.endswith("USDT"):
            base = symbol_raw.replace("USDT", "")
            symbol = f"{base}/USDT"
        else:
            # fallback: kalau user sudah ketik BTC/USDT
            symbol = symbol_raw

        bot.reply_to(message, f"⏳ Mengambil chart {symbol} timeframe {tf} dari {EXCHANGE_NAME}...")

        file_path = plot_chart_with_macd(symbol, tf)
        if not file_path:
            bot.reply_to(message, "Gagal membuat chart. Coba timeframe lain atau cek simbolnya lagi.")
            return

        with open(file_path, "rb") as photo:
            caption = f"{symbol} - {tf} (Price + MACD)"
            bot.send_photo(message.chat.id, photo, caption=caption)
    except Exception as e:
        bot.reply_to(message, f"Terjadi error saat membuat chart: `{e}`", parse_mode="Markdown")

# =========================
#  MAIN
# =========================

if __name__ == "__main__":
    print("🤖 Crypto MACD Signal Bot aktif.")
    print("   - CRYPTO only (Binance via ccxt)")
    print("   - Auto scan & kirim sinyal ke Telegram")
    print("   - Pair:", ", ".join(CRYPTO_PAIRS))
    print("   - TF:", ", ".join(CRYPTO_TIMEFRAMES))
    print("   - Fitur /tf untuk chart manual")

    # Jalankan web server (keep-alive)
    t_web = threading.Thread(target=run_web, daemon=True)
    t_web.start()

    # Jalankan scanner crypto
    t_crypto = threading.Thread(target=crypto_scanner_loop, daemon=True)
    t_crypto.start()

    # Jalankan Telegram bot
    bot.infinity_polling(timeout=60, long_polling_timeout=30)#  CRYPTO CONFIG (Binance via ccxt)
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
