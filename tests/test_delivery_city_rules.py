"""Delivery-city pattern "<organisation>, <town>, <region>" plus an unrelated head-office city (r07)."""
from pathlib import Path

from app.email_parser import parse_email
from app.models import ExtractedItem, ExtractedOrder
from app.steps.extract import FIELD_RULES
from app.steps.grounding import check_grounding

RAW = (Path(__file__).parent / "robustness_emails" / "r07_town_region_city.txt").read_text(encoding="utf-8")
TEXT = parse_email(RAW).searchable_text


def order(city):
    return ExtractedOrder(customer="Greenleaf Traders", delivery_city=city, deadline="2026-11-30",
                          deadline_text="30 November 2026", refers_to_previous_order=False,
                          items=[ExtractedItem(product_name="Volt 65W Charger", quantity=8, quantity_text="8")])


def test_last_place_in_delivery_address_is_grounded():
    assert check_grounding(order("Maharashtra"), TEXT) == []


def test_city_not_in_the_email_is_flagged():
    assert check_grounding(order("Mumbai"), TEXT) == ["Could not find delivery city 'Mumbai' in the email"]


def test_grounding_alone_cannot_choose_between_places_in_the_email():
    # Alandi and Chennai both appear in the email, so presence-grounding passes them. Choosing the right one is
    # the job of the shared field rules (extract prompt + verifier), so pin that wording down.
    assert check_grounding(order("Alandi"), TEXT) == []
    assert check_grounding(order("Chennai"), TEXT) == []
    assert "LAST" in FIELD_RULES and "delivery address" in FIELD_RULES
    assert "head office" in FIELD_RULES.lower() or "own address" in FIELD_RULES.lower()
