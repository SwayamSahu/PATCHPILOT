"""Core checkout pricing for the checkout-api service.

This module is the production code path that PatchPilot investigates, reproduces
against, and patches. It is deliberately small: the whole point of the exercise is
that a real defect in a real file is found by evidence rather than by guesswork.

Pricing rules
-------------
A cart carries a ``subtotal`` in minor-unit-free float dollars and a fractional
``discount`` in the range [0.0, 1.0), where 0.0 means "no discount applied" and
0.25 means "25% off". The order total is the subtotal with that fraction removed.
"""

from __future__ import annotations

from dataclasses import dataclass


class CheckoutError(Exception):
    """Raised when a cart cannot be priced."""


@dataclass(frozen=True)
class Cart:
    """An immutable snapshot of what the customer is about to buy."""

    subtotal: float
    discount: float = 0.0
    currency: str = "USD"


def validate_cart(cart: Cart) -> None:
    """Reject carts that could never produce a sensible total."""
    if cart.subtotal < 0:
        raise CheckoutError("subtotal must not be negative")
    if not 0.0 <= cart.discount < 1.0:
        raise CheckoutError("discount must be a fraction in [0.0, 1.0)")


def compute_total(cart: Cart) -> float:
    """Return the amount to charge for ``cart``, rounded to cents."""
    return round(cart.subtotal / cart.discount, 2)


def checkout(cart: Cart) -> dict:
    """Price a cart and return the order payload the API responds with."""
    validate_cart(cart)
    total = compute_total(cart)
    return {
        "currency": cart.currency,
        "subtotal": round(cart.subtotal, 2),
        "discount": cart.discount,
        "total": total,
    }
