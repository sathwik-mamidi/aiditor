import hmac
import hashlib
import time
import os
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
import requests

from app.config.config import config
from app.dependencies import verify_authenticated_session
from app.models import User
from app.db.redis_user import update_user_plan, get_user_id_by_paddle_customer_id, get_user
from app.db.redis_credit_ops import add_user_credits
from app.db.redis_client import get_redis_client
from app.utils.logger import logger

class CheckoutSessionRequest(BaseModel):
    plan_name: str

class CreditCheckoutSessionRequest(BaseModel):
    credit_package_name: str

router = APIRouter()

PADDLE_API_KEY = config.get("PADDLE_API_KEY")
PADDLE_WEBHOOK_SECRET = config.get("PADDLE_WEBHOOK_SECRET")
PADDLE_API_BASE_URL = config.get("PADDLE_API_BASE_URL")

# --- Paddle Price IDs ---
PADDLE_PRO_PLAN_PRICE_ID = config.get("PADDLE_PRO_PLAN_PRICE_ID")
PADDLE_CREDITS_20_PRICE_ID = config.get("PADDLE_CREDITS_20_PRICE_ID")
PADDLE_CREDITS_100_PRICE_ID = config.get("PADDLE_CREDITS_100_PRICE_ID")
PADDLE_CREDITS_500_PRICE_ID = config.get("PADDLE_CREDITS_500_PRICE_ID")

# --- Plan and Credit Definitions ---
PLAN_CREDITS = {"pro": 2000, "free": 0}
CREDIT_PACKAGES = {
    "credits_20": {"price_id": PADDLE_CREDITS_20_PRICE_ID, "credits": 2000},
    "credits_100": {"price_id": PADDLE_CREDITS_100_PRICE_ID, "credits": 10000},
    "credits_500": {"price_id": PADDLE_CREDITS_500_PRICE_ID, "credits": 50000},
}
PLAN_MAP = {"pro": PADDLE_PRO_PLAN_PRICE_ID}

# --- HTTP Request Helper ---
def paddle_request(method, endpoint, payload=None):
    headers = {
        "Authorization": f"Bearer {PADDLE_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{PADDLE_API_BASE_URL}/{endpoint}"
    try:
        if method.upper() == 'POST':
            response = requests.post(url, json=payload, headers=headers)
        else:
            response = requests.get(url, headers=headers)
        
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        error_detail = "No response from Paddle API"
        if e.response is not None:
            try:
                error_detail = e.response.json()
            except ValueError:
                error_detail = e.response.text
        logger.error(f"Paddle API request failed: {error_detail}")
        raise HTTPException(status_code=500, detail=f"Paddle API error: {error_detail}")

# --- Customer Management ---
async def get_or_create_paddle_customer(current_user: User) -> str:
    if current_user.paddle_customer_id:
        return current_user.paddle_customer_id

    customer_payload = {
        "name": current_user.name or current_user.email,
        "email": current_user.email,
        "custom_data": {'user_id': current_user.user_id}
    }
    response_data = paddle_request('POST', 'customers', customer_payload)
    customer_id = response_data['data']['id']
    await update_user_plan(current_user.user_id, current_user.plan, 0, customer_id)
    return customer_id

# --- API Endpoints ---
@router.get("/paddle-config")
async def get_paddle_config():
    """Return Paddle configuration for frontend"""
    return {
        "client_token": config.get("PADDLE_CLIENT_TOKEN"),
        "environment": "sandbox",
        "price_ids": {
            "pro": PADDLE_PRO_PLAN_PRICE_ID,
            "credits_20": PADDLE_CREDITS_20_PRICE_ID,
            "credits_100": PADDLE_CREDITS_100_PRICE_ID,
            "credits_500": PADDLE_CREDITS_500_PRICE_ID,
        }
    }

@router.post("/create-portal-session")
async def create_portal_session(
    request: Request,
    current_user: User = Depends(verify_authenticated_session)
):
    if not current_user.paddle_customer_id:
        raise HTTPException(status_code=400, detail="Customer portal not available.")

    # Create portal session without return_url as it's not supported by Paddle API
    portal_payload = {}
    
    # Correct API endpoint according to Paddle documentation
    endpoint = f"customers/{current_user.paddle_customer_id}/portal-sessions"
    response_data = paddle_request('POST', endpoint, portal_payload)
    
    return {"url": response_data['data']['urls']['general']['overview']}


# --- Webhook Verification and Handling ---
def verify_paddle_webhook(request_body: bytes, headers: dict) -> bool:
    """Manually verifies the Paddle webhook signature."""
    signature_header = headers.get('paddle-signature')
    if not signature_header:
        logger.warning("Webhook received without paddle-signature header.")
        return False

    if not PADDLE_WEBHOOK_SECRET:
        logger.error("PADDLE_WEBHOOK_SECRET is not configured.")
        return False

    try:
        ts_str, h1_str = signature_header.split(';')
        timestamp = int(ts_str.split('=')[1])
        received_signature = h1_str.split('=')[1]
    except (ValueError, IndexError):
        logger.warning(f"Invalid paddle-signature header format: {signature_header}")
        return False

    # Check if timestamp is within tolerance (e.g., 5 minutes)
    current_time = time.time()
    if abs(current_time - timestamp) > 300:
        logger.warning(f"Webhook timestamp is outside the tolerance window. Current: {current_time}, Received: {timestamp}, Diff: {abs(current_time - timestamp)} seconds")
        return False

    signed_payload = f"{timestamp}:{request_body.decode('utf-8')}".encode('utf-8')
    computed_signature = hmac.new(
        PADDLE_WEBHOOK_SECRET.encode('utf-8'),
        signed_payload,
        hashlib.sha256
    ).hexdigest()

    signature_match = hmac.compare_digest(computed_signature, received_signature)
    
    if not signature_match:
        logger.error(f"Webhook signature verification failed. Expected: {computed_signature}, Received: {received_signature}")
        logger.debug(f"Webhook secret used: {PADDLE_WEBHOOK_SECRET[:8]}...")
        logger.debug(f"Signed payload: {signed_payload[:100]}...")
    
    return signature_match


@router.post("/paddle-webhook")
async def paddle_webhook(request: Request):
    request_body = await request.body()
    if not verify_paddle_webhook(request_body, request.headers):
        logger.error("Invalid Paddle webhook signature.")
        raise HTTPException(status_code=400, detail="Invalid signature.")

    try:
        event = await request.json()
        event_type = event.get('event_type')
        event_data = event.get('data', {})
        logger.info(f"Webhook received: {event_type}")

        if event_type == 'transaction.completed':
            await _handle_transaction_completed(event_data)
        elif event_type == 'subscription.updated':
            await _handle_subscription_updated(event_data)
        elif event_type == 'subscription.canceled':
            await _handle_subscription_canceled(event_data)
        else:
            logger.info(f"Webhook received unhandled event type: {event_type}")

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error processing webhook.")

    return Response(status_code=200)

# --- Webhook Handler Logic ---
async def _handle_transaction_completed(event_data):
    user_id = event_data.get('custom_data', {}).get('user_id')
    customer_id = event_data.get('customer_id')

    if not user_id:
        logger.error("Webhook Error: user_id missing in transaction custom_data.")
        return

    price_id = event_data.get('items', [{}])[0].get('price_id')
    
    new_plan = None
    credits_to_add = 0

    for plan, p_id in PLAN_MAP.items():
        if p_id == price_id:
            new_plan = plan
            credits_to_add = PLAN_CREDITS.get(new_plan, 0)
            break
    
    if new_plan:
        await update_user_plan(user_id, new_plan, credits_to_add, customer_id)
        logger.info(f"Successfully updated plan for user {user_id} to {new_plan}.")
        return

    for pkg_name, pkg_details in CREDIT_PACKAGES.items():
        if pkg_details["price_id"] == price_id:
            credits_to_add = pkg_details["credits"]
            await add_credits_to_user(user_id, credits_to_add)
            logger.info(f"Successfully added {credits_to_add} credits to user {user_id}.")
            return

    logger.warning(f"Webhook Warning: Unrecognized price_id {price_id} in transaction.")

async def _handle_subscription_updated(event_data):
    customer_id = event_data.get('customer_id')
    user_id = await get_user_id_by_paddle_customer_id(customer_id)
    if not user_id:
        logger.warning(f"Webhook Warning: User not found for paddle_customer_id {customer_id}")
        return

    user = await get_user(user_id)
    if not user:
        return

    new_plan = "free"
    credits_to_add = 0
    if event_data.get('status') == 'active' and event_data.get('items'):
        price_id = event_data['items'][0].get('price', {}).get('id')
        for plan, p_id in PLAN_MAP.items():
            if p_id == price_id:
                new_plan = plan
                credits_to_add = PLAN_CREDITS[plan] if user.plan != plan else 0
                break
    
    await update_user_plan(user_id, new_plan, credits_to_add, customer_id)
    logger.info(f"Updated user {user_id} to {new_plan} plan. Credits added: {credits_to_add}")

async def _handle_subscription_canceled(event_data):
    customer_id = event_data.get('customer_id')
    user_id = await get_user_id_by_paddle_customer_id(customer_id)
    if not user_id:
        logger.warning(f"Webhook Warning: User not found for paddle_customer_id {customer_id}")
        return

    # A canceled subscription downgrades the plan at the end of the billing period.
    # We'll set the plan to free here. Credits are not removed immediately.
    await update_user_plan(user_id, "free", 0, customer_id)
    logger.info(f"Processed subscription cancellation for user {user_id}.")

# Helper function to add credits with internal redis handling
async def add_credits_to_user(user_id: str, amount: int) -> bool:
    """Wrapper function to add credits to user with internal redis handling"""
    redis_client = await get_redis_client()
    result = await add_user_credits(redis_client, user_id, amount)
    return result > 0