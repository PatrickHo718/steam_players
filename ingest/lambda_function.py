import json
import logging
import os
import time
import urllib.request
import urllib.error
from decimal import Decimal

import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

APP_ID = "1174180"
GAME_NAME = "Red Dead Redemption 2"
STEAM_API_URL = f"https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={APP_ID}"
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "rdr2-player-counts")


def fetch_player_count():
    """
    Fetches the current player count for Red Dead Redemption 2 from the Steam API.
    Returns the player count as an integer, or None if there was an error.
    """

    logger.info(f"Fetching player count from Steam API for app {APP_ID}")
    try:
        req = urllib.request.Request(STEAM_API_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
            logger.info(f"Raw API response: {raw}")
            data = json.loads(raw)

        if data.get("response", {}).get("result") != 1:
            logger.error(f"Steam API returned non-success result: {data}")
            return None

        player_count = data["response"]["player_count"]
        logger.info(f"Successfully fetched player count: {player_count}")
        return player_count

    except urllib.error.HTTPError as e:
        logger.error(f"HTTP error fetching player count: {e.code} {e.reason}")
        return None
    except urllib.error.URLError as e:
        logger.error(f"URL error fetching player count: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Steam API response: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching player count: {e}", exc_info=True)
        return None


def write_to_dynamodb(player_count):
    """
    Writes the player count to DynamoDB with a timestamp.
    Returns True if successful, False otherwise.
    """
    logger.info(f"Writing player count {player_count} to DynamoDB table: {DYNAMODB_TABLE}")
    try:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(DYNAMODB_TABLE)

        timestamp = int(time.time())
        item = {
            "game_id": APP_ID,
            "timestamp": timestamp,
            "player_count": Decimal(str(player_count)),
            "game_name": GAME_NAME,
        }

        logger.info(f"Putting item: {item}")
        table.put_item(Item=item)
        logger.info(f"Successfully wrote record to DynamoDB: timestamp={timestamp}, player_count={player_count}")
        return True

    except Exception as e:
        logger.error(f"Error writing to DynamoDB: {e}", exc_info=True)
        return False


def lambda_handler(event, context):
    """
    AWS Lambda entry point. Fetches player count and writes it to DynamoDB.
    """
    logger.info("Lambda function started.")
    logger.info(f"Event: {json.dumps(event)}")
    logger.info(f"Using DynamoDB table: {DYNAMODB_TABLE}")

    player_count = fetch_player_count()
    if player_count is None:
        logger.error("Failed to fetch player count — aborting.")
        return {"statusCode": 500, "body": "Failed to fetch player count from Steam API."}

    logger.info(f"About to write player count {player_count} to DynamoDB...")
    success = write_to_dynamodb(player_count)
    if not success:
        logger.error("Failed to write to DynamoDB.")
        return {"statusCode": 500, "body": "Failed to write to DynamoDB."}

    logger.info("Lambda completed successfully.")
    return {
        "statusCode": 200,
        "body": json.dumps({"player_count": player_count, "game_id": APP_ID}),
    }