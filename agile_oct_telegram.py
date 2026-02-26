import requests
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import os
import asyncio
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Bot
from loguru import logger
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# --- CONFIG ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_PATH = os.path.join(BASE_DIR, "agile_prices.png")
TABLE_IMG_PATH = os.path.join(BASE_DIR, "price_table_dark.png")
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
GO_PRICE = 9

charge_rate_30_min = 2.2

def sum_current_and_next_n(series, n):
    """Sum current row plus next n rows."""
    return series.rolling(window=n+1, min_periods=1).sum().shift(-n)

def process_prices(rates, go_sc=41.74, agile_sc=59.26):
    """
    Convert rates JSON to a DataFrame with forward-looking sums,
    cheaper options, and price differences.
    """
    df = pd.DataFrame(rates).sort_values(by="valid_from")
    df['cost'] = df['value_exc_vat'] * charge_rate_30_min

    duration_cols = [f"cost_for_{0.5*n+0.5}_hours" for n in range(1, 8)]
    for n, col in enumerate(duration_cols, start=1):
        df[col] = sum_current_and_next_n(df["cost"], n)

    # Compute minima per duration column
    min_prices = df[duration_cols].min()
    idx_min = df[duration_cols].idxmin()

    result = pd.DataFrame({
        "hours": [float(col.split('_')[2]) for col in duration_cols],
        "start_time": df.loc[idx_min, "valid_from"].values
    })

    # Compute go and agile prices
    result['go_price'] = GO_PRICE * result.hours * 2 * charge_rate_30_min + go_sc
    result['agile_price'] = min_prices.values + agile_sc

    # Cheaper option & difference
    result["winner"] = np.where(result["go_price"] < result["agile_price"], "go", "agile")
    result["price_diff"] = (result["go_price"] - result["agile_price"]).abs()
    result["rate"] = np.where(
        result["winner"] == "go",
        result["go_price"] / (result["hours"] * 2 * charge_rate_30_min ),
        result["agile_price"] / (result["hours"] * 2 * charge_rate_30_min ),
    )

    # Format start_time and round numbers
    # Convert start_time to datetime
    start_dt = pd.to_datetime(result["start_time"])
    
    # Create separate columns
    result["date"] = start_dt.dt.date
    result["time"] = start_dt.dt.time
    
    # Optional: drop the original start_time column
    result = result.drop(columns=["start_time"], errors="ignore")
    result = result.round(1)

    return result

def plot_price_table(df, figsize=(10,2), dark_mode=True, save_path=None):
    """
    Plot a DataFrame as a dark-mode table (or light-mode if dark_mode=False).
    """
    fig, ax = plt.subplots(figsize=figsize)
    ax.axis('off')
    
    if dark_mode:
        fig.patch.set_facecolor('#2e2e2e')
        ax.set_facecolor('#2e2e2e')
        header_color = '#1f1f1f'
        row_color = '#2e2e2e'
        text_color = 'white'
    else:
        header_color = '#f0f0f0'
        row_color = 'white'
        text_color = 'black'

    table = ax.table(cellText=df.values,
                     colLabels=df.columns,
                     cellLoc='center',
                     loc='center')

    for (i, j), cell in table.get_celld().items():
        cell.set_edgecolor('white')
        if i == 0:  # header
            cell.set_facecolor(header_color)
            cell.set_text_props(color=text_color, weight='bold')
        else:
            cell.set_facecolor(row_color)
            cell.set_text_props(color=text_color)

    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.5)

    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.show()


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
        for r in rates_sorted
    ]

    time_labels = [t.strftime("%H:%M") for t in times]
    prices = [r["value_inc_vat"] for r in rates_sorted]

    date_str = times[0].strftime("%Y-%m-%d")

    plt.style.use('dark_background')
    plt.figure(figsize=(12, 5))
    plt.step(time_labels, prices, where='post', color='deepskyblue', linewidth=2)

    # --- GO RATE LOGIC ---
    OFFPEAK_START = datetime.strptime("00:30", "%H:%M").time()
    OFFPEAK_END = datetime.strptime("05:30", "%H:%M").time()

    def is_offpeak(t):
        return OFFPEAK_START <= t.time() < OFFPEAK_END

    def go_rate_for_time(t):
        return GO_PRICE if is_offpeak(t) else 30.92

    # Plot dynamic Go rate
    go_rates = [go_rate_for_time(t) for t in times]
    plt.step(time_labels, go_rates, where="post", color="darkviolet", linestyle="--", linewidth=1.5)

    # --- Highlight logic ---
    threshold = 27.8

    for i, price in enumerate(prices):
        t = times[i]
        label = time_labels[i]

        if price > threshold:
            plt.scatter(label, price, color='tomato', s=100, zorder=5)
            plt.text(label, price + 0.3, label, color='tomato', fontsize=8, ha='center', va='bottom')

        elif price <= 0:
            plt.scatter(label, price, color='lime', s=100, zorder=5)
            plt.text(label, price + 0.3, label, color='lime', fontsize=8, ha='center', va='bottom')

        # VIOLET only when agile < go rate during off-peak
        elif price <= GO_PRICE:
            plt.scatter(label, price, color='violet', s=100, zorder=5)
            plt.text(label, price + 0.3, label, color='violet', fontsize=8, ha='center', va='bottom')

    # Text labels
    plt.text(
        time_labels[0],
        30.92 + 0.5,
        "Go Rate: 30.92p peak / 8.5p off-peak",
        color='violet',
        fontsize=10,
        ha='left'
    )

    plt.axhline(y=threshold, color='orange', linestyle='--', linewidth=1.5)
    plt.text(time_labels[0], threshold + 0.3, f"Threshold: {threshold}p", color='orange', fontsize=10)

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


async def send_table():
    logger.info("Sending table to Telegram...")
    bot = Bot(token=BOT_TOKEN)
    with open(TABLE_IMG_PATH, "rb") as img:
        await bot.send_photo(chat_id=CHAT_ID, photo=img, caption="📊 Optimum tarriff tomorow")
    logger.info("Table sent successfully.")


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
        result_df = process_prices(rates)
        plot_price_table(result_df, save_path=f"{BASE_DIR}/price_table_dark.png")
        await send_chart()
        await send_table()
        mark_as_run_today()
    except Exception as e:
        logger.exception("An error occurred")
        await send_error(str(e))


if __name__ == "__main__":
    logger.info("===== Script started =====")
    asyncio.run(main())
    logger.info("===== Script finished =====")
