import logging
import os
from typing import Dict, List

import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.security import HTTPBearer
from pydantic import ValidationError

from models import CartDetails, Order, OrderRequest, Payment, PaymentRequest

app = FastAPI()
SECRET_KEY = "mysecretkey"
logger = logging.getLogger(__name__)
security = HTTPBearer()

# In-memory order store
payments: Dict[str, Payment] = {}

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://orders-svc.api")
PAYMENTS_SERVICE_URL = os.getenv(
    "PAYMENTS_SERVICE_URL", "http://payments-svc.api"
)
USER_SERVICE_URL = os.getenv("USER_SVC_URL", "http://payments-svc.api")
PRODUCTS_SERVICE_URL = os.getenv(
    "PRODUCTS_SERVICE_URL", "http://product-svc.api/products"
)


@app.get("/healthz", status_code=201)
async def health():
    return {"message": "Hello from Api Gateway service!"}


protected_routes = ["/api/v1/orders/create", "/api/v1/payments"]


def verify_token(authorisation_header: str):
    try:
        token = authorisation_header.split("Bearer ")[1]
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload["user_id"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# @app.get("/api/v1/products")
# def get_products():
#     response = requests.get(f"{PRODUCTS_SERVICE_URL}/products")
#     return response.json()

# @app.post("/api/v1/orders")
# def create_order(request: Request, user_id: str = Depends(verify_token)):
#     body = request.json()
#     body["user_id"] = user_id
#     response = requests.post("http://orders-service:8002/orders", json=body)
#     return response.json()

# @app.post("/api/v1/payments")
# def process_payment(request: Request, user_id: str = Depends(verify_token)):
#     body = request.json()
#     body["user_id"] = user_id
#     response = requests.post("http://payments-service:8003/payments", json=body)
#     return response.json()


async def calculate_order_value(product_ids: List[str]) -> float:
    async with httpx.AsyncClient() as client:
        amts = []
        for pid in product_ids:
            try:
                response = await client.get(f"{PRODUCTS_SERVICE_URL}/{pid}")
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=int(response.status_code),
                        detail=response.json(),
                    )
                product = response.json()
                amt = float(
                    f"{product["priceUsd"]["units"]}.{product["priceUsd"]["units"]}"
                )
                amts.append(amt)
            except httpx.RequestError as e:
                logger.error(str(e) if str(e) else repr(e))
                raise HTTPException(
                    status_code=500, detail="Error contacting external API"
                )
        return sum(amts)


async def get_cart_amount(order_id: str):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{ORDER_SERVICE_URL}/orders/{order_id}"
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code, detail=response.json()
                )
            product_ids = response.json()["product_ids"]
            amt = await calculate_order_value(product_ids=product_ids)
            return amt
        except httpx.RequestError as e:
            logger.error(str(e) if str(e) else repr(e))
            raise HTTPException(
                status_code=500, detail="Error contacting external API"
            )


@app.api_route(
    "/api/v1/products/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
)
async def proxy_products(full_path: str, request: Request):
    async with httpx.AsyncClient() as client:
        # Construct the new URL by stripping /api/v1/products
        url = (
            f"{PRODUCTS_SERVICE_URL}/{full_path}"
            if full_path
            else PRODUCTS_SERVICE_URL
        )

        # Forward the request while keeping method, headers, and body
        print(request.method)
        response = await client.request(
            method=request.method,
            url=url,
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower() != "host"
            },
            content=await request.body(),
        )

        return (
            response.json()
            if response.headers.get("content-type") == "application/json"
            else response.text
        )


@app.post("/api/v1/order/create", response_model=CartDetails)
async def create_order(order: OrderRequest, Authorization: str = Header()):
    user_id = verify_token(Authorization)
    try:
        cart_order = None
        created_order = Order.model_validate(
            {
                "user_id": user_id,
                "product_ids": order.product_ids,
            }
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{ORDER_SERVICE_URL}/orders", json=created_order.model_dump()
            )

            if response.status_code == 200:
                cart_order = response.json()
                assert cart_order is not None
            else:
                # Raise error for non-200 responses
                error_data = response.json()
                raise HTTPException(
                    status_code=response.status_code, detail=error_data
                )

        cart_details = CartDetails.model_validate(
            {
                "order": cart_order,
                "amount": await calculate_order_value(order.product_ids),
            }
        )

        return cart_details

    except httpx.RequestError as e:
        logger.error(str(e) if str(e) else repr(e))
        raise HTTPException(
            status_code=500, detail="Error contacting external API"
        )
    except ValidationError:
        raise HTTPException(status_code=400, detail="Malformed Request")
    except AssertionError as e:
        logger.error(f"Error: {str(e) if str(e) else repr(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.post("/api/v1/payments/pay", response_model=Payment)
async def make_payment(
    payment_request: PaymentRequest, Authorization: str = Header()
):
    user_id = verify_token(Authorization)
    try:
        order_value = await get_cart_amount(payment_request.order_id)
        assert order_value == payment_request.amount
        created_payment = Payment.model_validate(
            {
                "user_id": user_id,
                "order_id": payment_request.order_id,
                "amount": order_value,
            }
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYMENTS_SERVICE_URL}/payments",
                json=created_payment.model_dump(),
            )

            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Payment Failed")

            return Payment.model_validate(response.json())
    except httpx.RequestError as e:
        logger.error(str(e) if str(e) else repr(e))
        raise HTTPException(
            status_code=500, detail="Error contacting external API"
        )
    except AssertionError:
        raise HTTPException(status_code=400, detail="Invalid Amount")


@app.get("/api/v1/payments/status/{payment_id}", response_model=Payment)
async def check_payment_status(payment_id: str, Authorization: str = Header()):
    verify_token(Authorization)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{PAYMENTS_SERVICE_URL}/payments/{payment_id}"
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code, detail=response.json()
                )
            return Payment.model_validate(response.json())
    except httpx.RequestError as e:
        logger.error(str(e) if str(e) else repr(e))
        raise HTTPException(
            status_code=500, detail="Error contacting external API"
        )
