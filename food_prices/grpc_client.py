"""
gRPC odjemalec (stub ovojnica) za spletno aplikacijo Flask (Anti-Wolt).

Flask strežnik (`app.py`) uporablja ta modul kot gRPC odjemalca za komunikacijo z
oddaljenim oz. ločenim gRPC strežnikom (`grpc_server.py`) prek binarnega protokola
Protocol Buffers (HTTP/2). Pretvarja prejeta Protobuf sporočila v Python slovarje,
ki jih Flask nato posreduje brskalniku (prek JSON oz. SSE `text/event-stream`).
"""

import os
import threading
from typing import Dict, List, Any, Optional, Iterator

import grpc

from food_prices.protos import anti_wolt_pb2, anti_wolt_pb2_grpc


def _inflation_pb_to_dict(item: anti_wolt_pb2.InflationItem) -> Dict[str, Any]:
    """Skupni pretvornik Protobuf sporočila `InflationItem` v Python slovar."""
    return {
        "product_name": item.product_name,
        "unit": item.unit,
        "category": item.category,
        "start_price": round(float(item.start_price), 2),
        "end_price": round(float(item.end_price), 2),
        "diff_eur": round(float(item.diff_eur), 2),
        "diff_pct": round(float(item.diff_pct), 1),
    }


class AntiWoltGrpcClient:
    """
    Nitno varen gRPC odjemalec za storitev `AntiWoltService`.
    Vzdržuje ponovno uporaben kanal (channel) in nastavek (stub) do gRPC strežnika.
    """

    def __init__(self, target: Optional[str] = None, timeout_sec: float = 5.0):
        self.target = target or os.getenv("GRPC_TARGET", "127.0.0.1:50051")
        self.timeout_sec = timeout_sec
        self._lock = threading.Lock()
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[anti_wolt_pb2_grpc.AntiWoltServiceStub] = None

    def _get_stub(self) -> anti_wolt_pb2_grpc.AntiWoltServiceStub:
        """Leno inicializira in vrne gRPC nastavek (stub)."""
        with self._lock:
            if self._channel is None or self._stub is None:
                self._channel = grpc.insecure_channel(self.target)
                self._stub = anti_wolt_pb2_grpc.AntiWoltServiceStub(self._channel)
            return self._stub

    def is_available(self, timeout: float = 0.8) -> bool:
        """Preveri, ali je gRPC strežnik dosegljiv in pripravljen za sprejemanje zahtevkov."""
        try:
            self._get_stub()
            grpc.channel_ready_future(self._channel).result(timeout=timeout)
            return True
        except grpc.FutureTimeoutError:
            return False

    def get_filtered_prices(
        self,
        category: Optional[str] = None,
        year: Optional[int] = None,
        search: Optional[str] = None,
        only_food: bool = True,
    ) -> Dict[str, Any]:
        """Pokliče unary RPC `GetFilteredPrices` in vrne štetje, razrešeno leto ter zapise."""
        stub = self._get_stub()
        req = anti_wolt_pb2.PriceFilterRequest(
            category=category or "",
            year=int(year) if year else 0,
            search=search or "",
            only_food=only_food,
        )
        resp = stub.GetFilteredPrices(req, timeout=self.timeout_sec)
        records = [
            {
                "product_raw": item.product_raw,
                "product_name": item.product_name,
                "unit": item.unit,
                "category": item.category,
                "year": int(item.year),
                "price_eur": round(float(item.price_eur), 2),
            }
            for item in resp.items
        ]
        return {
            "count": int(resp.count),
            "resolved_year": int(resp.resolved_year),
            "data": records,
        }

    def get_product_history(self, product_query: str) -> Dict[str, Any]:
        """Pokliče unary RPC `GetProductHistory` in vrne časovno vrsto cene izdelka."""
        stub = self._get_stub()
        req = anti_wolt_pb2.ProductHistoryRequest(product=product_query)
        resp = stub.GetProductHistory(req, timeout=self.timeout_sec)

        if not resp.found:
            return {"found": False, "query": resp.query or product_query, "history": []}

        return {
            "found": True,
            "product_name": resp.product_name,
            "unit": resp.unit,
            "category": resp.category,
            "years": list(resp.years),
            "prices": [round(float(p), 2) for p in resp.prices],
            "start_year": int(resp.start_year),
            "end_year": int(resp.end_year),
            "start_price": round(float(resp.start_price), 2),
            "end_price": round(float(resp.end_price), 2),
            "diff_eur": round(float(resp.diff_eur), 2),
            "diff_pct": round(float(resp.diff_pct), 1),
        }

    def get_anti_wolt_comparison(self, year: int = 2025) -> Dict[str, Any]:
        """Pokliče unary RPC `GetAntiWoltComparison` za primerjavo domače kuhinje z dostavo."""
        stub = self._get_stub()
        req = anti_wolt_pb2.ComparisonRequest(year=int(year))
        resp = stub.GetAntiWoltComparison(req, timeout=self.timeout_sec)

        return {
            "year": int(resp.year),
            "average_savings_pct": round(float(resp.average_savings_pct), 1),
            "comparisons": [
                {
                    "meal": c.meal,
                    "home_title": c.home_title,
                    "home_cost": round(float(c.home_cost), 2),
                    "restaurant_title": c.restaurant_title,
                    "restaurant_cost": round(float(c.restaurant_cost), 2),
                    "savings_eur": round(float(c.savings_eur), 2),
                    "savings_pct": round(float(c.savings_pct), 1),
                }
                for c in resp.comparisons
            ],
        }

    def get_inflation_leaders(
        self,
        start_year: int = 2018,
        end_year: int = 2025,
        limit: int = 8,
    ) -> Dict[str, Any]:
        """Pokliče unary RPC `GetInflationLeaders` za najhitreje podražena živila."""
        stub = self._get_stub()
        req = anti_wolt_pb2.InflationRequest(
            start_year=int(start_year),
            end_year=int(end_year),
            limit=int(limit),
        )
        resp = stub.GetInflationLeaders(req, timeout=self.timeout_sec)
        return {
            "period": resp.period,
            "data": [_inflation_pb_to_dict(item) for item in resp.items],
        }

    def stream_category_inflation(
        self,
        category: Optional[str] = None,
        start_year: int = 2018,
        end_year: int = 2025,
        delay_ms: int = 120,
    ) -> Iterator[Dict[str, Any]]:
        """
        Pokliče server-side streaming RPC `StreamCategoryInflation` in sproti
        vrača prejete dogodke, kot prihajajo iz gRPC strežnika.
        """
        stub = self._get_stub()
        req = anti_wolt_pb2.CategoryInflationStreamRequest(
            category=category or "Vse",
            start_year=int(start_year),
            end_year=int(end_year),
            delay_ms=int(delay_ms),
        )
        stream = stub.StreamCategoryInflation(req, timeout=60.0)
        try:
            for event in stream:
                yield {
                    "item": _inflation_pb_to_dict(event.item),
                    "processed_count": int(event.processed_count),
                    "total_count": int(event.total_count),
                    "running_avg_pct": round(float(event.running_avg_pct), 1),
                }
        finally:
            # Zagotovimo prekinitev gRPC toka na strežniku, če generator zapustimo predčasno
            stream.cancel()

    def close(self) -> None:
        """Zapre odprti gRPC kanal ob zaustavitvi aplikacije."""
        with self._lock:
            if self._channel is not None:
                self._channel.close()
                self._channel = None
                self._stub = None
