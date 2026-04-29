import logging
import os
import time
import io
from decimal import Decimal
from datetime import datetime, timezone

import boto3
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend — must be set before pyplot import
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
 
from boto3.dynamodb.conditions import Key
from chalice import Chalice
 
app = Chalice(app_name="rdr2-tracker")

# Logging configuration
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Constants
APP_ID = "1174180"  # Red Dead Redemption 2
GAME_NAME = "Red Dead Redemption 2"
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "rdr2-player-counts")
S3_bucket = os.environ.get("S3_BUCKET", "rdr2-tracker-plots")
S3_PLOT_KEY = "rdr2/latest.png"

def get_dynamodb_table():
    """
    Helper function to get the DynamoDB table resource.
    """
    dynamodb = boto3.resource('dynamodb')
    return dynamodb.Table(DYNAMODB_TABLE)

def query_recent_data(hours=24):
    """
    Queries DynamoDB for player count data from the last specified hours.
    """
    try: 
        table = get_dynamodb_table()
        now = int(time.time())
        cutoff = now - (hours * 3600)
        
        response = table.query(
            KeyConditionExpression=Key('game_id').eq(APP_ID) & Key('timestamp').gte(cutoff),
            ScanIndexForward=True  # Ascending order
        )
        
        items = response.get('Items', [])
        logger.info(f"Queried {len(items)} items from DynamoDB for the last {hours} hours.")
        return items
    except Exception as e:
        logger.error(f"Error querying DynamoDB: {e}")
        return []
    
def generate_plot(items):
    """Build a time-series chart from items and upload to S3. Returns public URL."""
    logger.info(f"Rendering plot from {len(items)} data points")
    try:
        # Sort ascending for plotting
        sorted_items = sorted(items, key=lambda x: int(x["timestamp"]))
        timestamps = [
            datetime.fromtimestamp(int(r["timestamp"]), tz=timezone.utc)
            for r in sorted_items
        ]
        counts = [int(r["player_count"]) for r in sorted_items]
 
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(timestamps, counts, color="#c0392b", linewidth=2, marker="o", markersize=3)
        ax.fill_between(timestamps, counts, alpha=0.15, color="#c0392b")
 
        ax.set_title(f"{GAME_NAME} — Steam Concurrent Players (Last 24 h)", fontsize=14)
        ax.set_xlabel("Time (UTC)")
        ax.set_ylabel("Players Online")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        fig.autofmt_xdate()
        ax.grid(True, linestyle="--", alpha=0.4)
        plt.tight_layout()
 
        # Save to bytes buffer
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
 
        # Upload to S3
        logger.info(f"Uploading plot to s3://{S3_bucket}/{S3_PLOT_KEY}")
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=S3_bucket,
            Key=S3_PLOT_KEY,
            Body=buf.getvalue(),
            ContentType="image/png",
        )
 
        url = f"https://{S3_bucket}.s3.amazonaws.com/{S3_PLOT_KEY}"
        logger.info(f"Plot uploaded successfully: {url}")
        return url
 
    except Exception as e:
        logger.error(f"Error rendering/uploading plot: {e}", exc_info=True)
        return None

# routes
@app.route("/")
def index():
    """Zone apex — describes the project and lists available resources."""
    logger.info("GET / called")
    return {
        "about": (
            f"Tracks Steam concurrent player counts for {GAME_NAME} (app {APP_ID}) "
            "every 30 minutes and exposes current count, 24-hour trend, and a time-series plot."
        ),
        "resources": ["current", "trend", "plot"],
    }

@app.route("/current")
def current():
    """
    Returns the most recent player count with timestamp.
    """
    try:
        table = get_dynamodb_table()
        response = table.query(
            KeyConditionExpression=Key('game_id').eq(APP_ID),
            ScanIndexForward=False,  # Descending order
            Limit=1
        )
        items = response.get('Items', [])
        if not items:
            logger.warning("No player count data found in DynamoDB.")
            return {"response": "No data available"}
        
        latest = items[0]
        count = int(latest["player_count"])
        timestamp = int(latest["timestamp"])
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        msg = f"{GAME_NAME} currently has {count:,} players on Steam (as of {dt})."
        logger.info(f"/current response: {msg}")
        return {"response": msg}
    except Exception as e:
        logger.error(f"Error in /current route: {e}", exc_info=True)
        return {"response": "Error fetching current player count"}

@app.route("/trend")
def trend():
    """
    Return avg, min, max, and delta over 24 hours
    """
    logger.info("GET /trend called")
    try:
        items = query_recent_data(hours=24)
        if not items:
            logger.warning("No data available for trend calculation.")
            return {"response": "No data available for trend"}

        counts = [int(r["player_count"]) for r in items]
        avg = sum(counts) / len(counts)
        peak = max(counts)
        low = min(counts)

        # delta
        newest = int(items[0]["player_count"])
        oldest = int(items[-1]["player_count"])
        delta = newest - oldest
        direction = "increased" if delta > 0 else "decreased"

        msg = (
            f"{GAME_NAME} over the last 24 hr: "
            f"avg: {avg:,.0f} | peak: {peak:,} | low: {low:,} | "
            f"trend: {direction} {abs(delta):,} players ({len(counts)} samples)."
        )
        logger.info(f"/trend response: {msg}")
        return {"response": msg}
    except Exception as e:
        logger.error(f"Error in /trend route: {e}", exc_info=True)
        return {"response": "Error calculating trend"}
    
@app.route("/plot")
def plot():
    """
    Returns the public URL of the latest plot image.
    """
    logger.info("GET /plot called")
    try:
        items = query_recent_data(hours=24)
        if len(items) < 2:
            logger.warning("Not enough data points to generate plot.")
            return {"response": "Not enough data to generate plot"}
        url = generate_plot(items)
        if url is None:
            return {"response": "Error generating plot"}
        logging.info(f"/plot response: {url}")
        return {"response": url}
    except Exception as e:
        logger.error(f"Error in /plot route: {e}", exc_info=True)
        return {"response": "Error generating plot"}