@bot.message_handler(commands=["start"])
def start_cmd(message):
    global USER_CHAT_ID
    USER_CHAT_ID = message.chat.id

    # === BUAT KEYBOARD MENU ===
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_crypto = types.KeyboardButton("CRYPTO")
    btn_chart = types.KeyboardButton("Chart")
    kb.row(btn_crypto, btn_chart)

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
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=kb)    app.run(host="0.0.0.0", port=8080)

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
