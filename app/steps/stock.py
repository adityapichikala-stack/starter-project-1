"""Step 3 (code): match names to the catalogue and compare with stock.

The database query (StockRepository) is kept separate from the comparison (check_stock),
so the comparison can be tested without a database. Stock is read-only.
"""
import logging
import time
from typing import Protocol

import pg8000.dbapi
from decimal import Decimal

from app.config import Settings
from app.models import CatalogueEntry, StockResult, OrderItem, ValidatedItem
from app.observability import current_metrics

pg8000.dbapi.paramstyle = "pyformat"  # named %(x)s parameters

log = logging.getLogger("stock")


def normalize_name(name: str) -> str:
    """Case-insensitive, extra spaces ignored. Nothing fuzzier: a similar name is not a match."""
    return " ".join(name.split()).lower()


class CatalogueLookup(Protocol):
    def find_by_names(self, names: list[str]) -> dict[str, CatalogueEntry]: ...


class StockRepository:
    # Fixed, parameterised query. The same normalisation as normalize_name(), done in SQL.
    _SQL = """
        SELECT p.product_code, p.name, s.quantity_available, p.unit_price_inr
        FROM products p
        JOIN stock s ON s.product_code = p.product_code
        WHERE lower(regexp_replace(btrim(p.name), '\\s+', ' ', 'g')) = ANY(%s)
    """

    def __init__(self, settings: Settings):
        self._conn_args = dict(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            database=settings.db_name,
            timeout=10,
        )

    def _connect(self):
        return pg8000.dbapi.connect(**self._conn_args)

    def ping(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()

    def find_by_names(self, names: list[str]) -> dict[str, CatalogueEntry]:
        """Returns {normalized name: entry} for the names that exist in the catalogue."""
        metrics = current_metrics()
        request_id = metrics.request_id if metrics else "-"
        started = time.perf_counter()
        keys = sorted({normalize_name(n) for n in names})
        if not keys:
            if metrics:
                metrics.add_step_duration("stock_query", 0)
            log.info(
                "event=stock_query_completed request_id=%s outcome=skipped duration_ms=0 names=0 matched=0",
                request_id,
            )
            return {}
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(self._SQL, (keys,))
                rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            if metrics:
                metrics.add_step_duration("stock_query", duration_ms)
            log.error(
                "event=stock_query_failed request_id=%s outcome=failed duration_ms=%.3f "
                "error_category=database error_type=%s",
                request_id,
                duration_ms,
                type(exc).__name__,
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        if metrics:
            metrics.add_step_duration("stock_query", duration_ms)
        log.info(
            "event=stock_query_completed request_id=%s outcome=success duration_ms=%.3f "
            "names=%d matched=%d",
            request_id,
            duration_ms,
            len(keys),
            len(rows),
        )
        return {
            normalize_name(name): CatalogueEntry(
                product_code=code,
                name=name,
                quantity_available=qty,
                unit_price_inr=Decimal(str(price)),
            )
            for code, name, qty, price in rows
        }

    def get_catalogue(self) -> dict[str, CatalogueEntry]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT p.product_code, p.name, s.quantity_available, p.unit_price_inr
                FROM products p
                JOIN stock s ON s.product_code = p.product_code
                """
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return {
            normalize_name(name): CatalogueEntry(
                product_code=code,
                name=name,
                quantity_available=qty,
                unit_price_inr=Decimal(str(price)),
            )
            for code, name, qty, price in rows
        }

    def find_history(self, customer: str) -> list[tuple[str, str, str, int]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT customer, order_date::text, product_code, quantity
                FROM order_history
                WHERE lower(btrim(customer)) = lower(btrim(%(customer)s))
                ORDER BY order_date DESC, order_id DESC
                """,
                {"customer": customer},
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [(str(a), str(b), str(c), int(d)) for a, b, c, d in rows]


def check_stock(items: list[ValidatedItem], catalogue: dict[str, CatalogueEntry]) -> StockResult:
    """Pure comparison of requested quantities against the catalogue/stock snapshot."""
    out: list[OrderItem] = []
    unknown: list[str] = []
    short: list[str] = []
    for it in items:
        entry = catalogue.get(normalize_name(it.product_name))
        if entry is None:
            unknown.append(f"Unknown product: {it.product_name}")
            out.append(OrderItem(product_name=it.product_name, product_code=None, requested=it.requested, available=None))
            continue
        out.append(
            OrderItem(
                product_name=entry.name,
                product_code=entry.product_code,
                requested=it.requested,
                available=entry.quantity_available,
            )
        )
        if it.requested is not None and it.requested > entry.quantity_available:
            short.append(f"{entry.name}: requested {it.requested}, only {entry.quantity_available} in stock")
    return StockResult(items=out, unknown_issues=unknown, shortage_issues=short)
