import requests
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import os
import asyncio
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Bot
from loguru import logger  # 🧩 Add Loguru

# --- CONFIG ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_PATH = os.path.join(BASE_DIR, "agile_prices.png")
LAST_RUN_FILE = os.path.join(BASE_DIR, "last_run.txt")
LOG_DIR = os.path.join(BASE_DIR, "logs")
PRODUCT_CODE = "AGILE-24-10-01"
TARIFF_CODE = "E-1R-AGILE-24-10-01-H"

# Create log directory if not exists
os.makedirs(LOG_DIR, exist_ok=True)

# 🧩 Configure Loguru: one file per day, keep 28 days
logger.add(
    os.path.join(LOG_DIR, "{time:YYYY-MM-DD}.log"),
    rotation="00:00",         # new file at midnight
    retention="28 days",      # keep logs for 28 days
    compression="zip",        # optional: compress old logs
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRODUCT_CODE = os.getenv("PRODUCT_CODE")
TARIFF_CODE = os.getenv("TARIFF_CODE")


def has_already_run_today():
    """Check if we've already posted today."""
    if not os.path.exists(LAST_RUN_FILE):
        logger.info("No previous run file found — first run today.")
        return False
    with open(LAST_RUN_FILE, "r") as f:
        last_run = f.read().strip()
    today = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")
    ran = last_run == today
    logger.info(f"Already ran today? {ran}")
    return ran


def mark_as_run_today():
    """Mark that we've posted today."""
    today = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")
    with open(LAST_RUN_FILE, "w") as f:
        f.write(today)
    logger.info("Marked as run for today.")


def fetch_tomorrow_rates():
    tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d')
    url = f"https://api.octopus.energy/v1/products/{PRODUCT_CODE}/electricity-tariffs/{TARIFF_CODE}/standard-unit-rates/"
    params = {
        "period_from": f"{tomorrow}T00:00Z",
        "period_to": f"{tomorrow}T23:30Z"
    }
    logger.info(f"Fetching rates for {tomorrow}")
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()["results"]
    logger.info(f"Fetched {len(data)} rate entries.")
    return data


def plot_prices(rates):
    london_tz = ZoneInfo("Europe/London")
    rates_sorted = sorted(rates, key=lambda r: r["valid_from"])

    times = [
        datetime.fromisoformat(r["valid_from"].replace("Z", "+00:00"))
        .astimezone(london_tz)
        .strftime("%H:%M")
        for r in rates_sorted
    ]

    prices = [r["value_inc_vat"] for r in rates_sorted]

    date_str = datetime.fromisoformat(rates_sorted[0]["valid_from"].replace("Z", "+00:00")).astimezone(london_tz).strftime("%Y-%m-%d")

    plt.style.use('dark_background')
    plt.figure(figsize=(12, 5))
    plt.step(times, prices, where='post', color='deepskyblue', linewidth=2)

    threshold = 25.94
    plt.axhline(y=threshold, color='orange', linestyle='--', linewidth=1.5)

    go_rate = 8.5
    plt.axhline(y=go_rate, color='darkviolet', linestyle='--', linewidth=1.5)

    for i, price in enumerate(prices):
        if price > threshold:
            plt.scatter(times[i], price, color='tomato', s=100, zorder=5)
            plt.text(times[i], price + 0.3, times[i], color='tomato', fontsize=8, ha='center', va='bottom')
        elif price <= go_rate:
            plt.scatter(times[i], price, color='violet', s=100, zorder=5)
            plt.text(times[i], price + 0.3, times[i], color='violet', fontsize=8, ha='center', va='bottom')
        elif price <= 0:
            plt.scatter(times[i], price, color='lime', s=100, zorder=5)
            plt.text(times[i], price + 0.3, times[i], color='lime', fontsize=8, ha='center', va='bottom')

    plt.text(times[0], threshold + 0.2, f"Threshold: {threshold}p/kWh", color='orange', fontsize=10, va='bottom', ha='left')
    plt.text(times[0], go_rate + 0.2, f"go_rate: {go_rate}p/kWh", color='violet', fontsize=10, va='bottom', ha='left')

    plt.xticks(rotation=90, color='white')
    plt.yticks(color='white')
    plt.grid(True, linestyle='--', color='gray', alpha=0.3)
    plt.title(f"⚡ Agile Octopus Prices for {date_str}", color='white', fontsize=14)
    plt.ylabel("p/kWh", color='white')

    plt.tight_layout()
    plt.savefig(IMG_PATH, facecolor='#111111')
    plt.close()

    logger.info(f"Chart saved for {date_str} at {IMG_PATH}")


async def send_chart():
    logger.info("Sending chart to Telegram...")
    bot = Bot(token=BOT_TOKEN)
    with open(IMG_PATH, "rb") as img:
        await bot.send_photo(chat_id=CHAT_ID, photo=img, caption="📊 Agile prices for tomorrow")
    logger.info("Chart sent successfully.")


async def send_error(message):
    logger.error(f"Sending error to Telegram: {message}")
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=f"❌ Error: {message}")


async def main():
    if has_already_run_today():
        logger.info("Script already ran today. Exiting.")
        return

    try:
        rates = fetch_tomorrow_rates()
        if not rates:
            raise ValueError("No rates returned")
        plot_prices(rates)
        await send_chart()
        mark_as_run_today()
    except Exception as e:
        logger.exception("An error occurred")
        await send_error(str(e))


if __name__ == "__main__":
    logger.info("===== Script started =====")
    asyncio.run(main())
    logger.info("===== Script finished =====")
