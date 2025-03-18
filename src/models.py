from enum import StrEnum
from typing import List

from pydantic import BaseModel


class OrderStatusEnum(StrEnum):
    PENDING = "pending"
    PAID = "paid"


class Order(BaseModel):
    id: str = ""
    user_id: str
    product_ids: List[str]
    status: OrderStatusEnum = OrderStatusEnum.PENDING


class OrderRequest(BaseModel):
    product_ids: List[str]


class Payment(BaseModel):
    id: str = ""
    user_id: str
    order_id: str
    amount: float
    status: str = ""


class CartDetails(BaseModel):
    order: Order
    amount: float


class PaymentRequest(BaseModel):
    order_id: str
    amount: float
