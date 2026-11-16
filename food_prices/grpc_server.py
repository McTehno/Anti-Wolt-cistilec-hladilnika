"""
Samostojni gRPC strežnik za projekt Anti-Wolt (OPSI / SiStat statistika cen živil).

Ta modul izpostavlja eno gRPC storitev (`AntiWoltService`) na portu 50051 z 5 RPC klici:
1. GetFilteredPrices       (Unary RPC)                 - filtriranje cen živil
2. GetProductHistory       (Unary RPC)                 - časovna vrsta cene izbranega živila
3. GetAntiWoltComparison   (Unary RPC)                 - primerjava domače priprave vs. gostinstva
4. GetInflationLeaders     (Unary RPC)                 - izdelki z največjo podražitvijo
5. StreamCategoryInflation (Server-Side Streaming RPC) - sprotna analiza podražitev po kategoriji

Strežnik uporablja binarno serializacijo Protocol Buffers in obstoječi procesor
`PriceDataProcessor` (pyaxis + pandas), zato je prenos med aplikacijo in strežnikom
hiter, strogo tipiziran in neodvisen od besedilnega formata JSON.
"""

import os
import sys
import time
import logging
import threading
from concurrent import futures
from typing import Dict, Any

import grpc

# Zagotovitev pravilnega UTF-8 izpisa na Windows konzolah
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from food_prices.processor import PriceDataProcessor
from food_prices.protos import anti_wolt_pb2, anti_wolt_pb2_grpc

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("food_prices.grpc_server")


def _build_inflation_item_pb(row: Dict[str, Any]) -> anti_wolt_pb2.InflationItem:
    """Skupni pretvornik slovarja inflacijske postavke v Protobuf sporočilo `InflationItem`."""
    return anti_wolt_pb2.InflationItem(
        product_name=row["product_name"],
        unit=row["unit"],
        category=row["category"],
        start_price=float(row["start_price"]),
        end_price=float(row["end_price"]),
        diff_eur=float(row["diff_eur"]),
        diff_pct=float(row["diff_pct"]),
    )


class AntiWoltServiceServicer(anti_wolt_pb2_grpc.AntiWoltServiceServicer):
    """
    Implementacija strežniških metod gRPC storitve `AntiWoltService`.
    """

    def __init__(self, processor: PriceDataProcessor | None = None):
        self._lock = threading.Lock()
        self.processor = processor or PriceDataProcessor()
        self._px_mtime = self._read_px_mtime()

    def _read_px_mtime(self) -> float:
        """Prebere čas zadnje spremembe predpomnjene .px datoteke na disku."""
        try:
            return os.path.getmtime(self.processor.px_path)
        except OSError:
            return 0.0

    def _get_fresh_processor(self) -> PriceDataProcessor:
        """
        Preveri, ali je bila .px datoteka na disku medtem posodobljena (npr. prek /api/refresh),
        in po potrebi varno ponovno naloži procesor podatkov.
        """
        with self._lock:
            current_mtime = self._read_px_mtime()
            if current_mtime and current_mtime != self._px_mtime:
                logger.info("Zaznana posodobljena .px datoteka na disku. Ponovno nalagam PriceDataProcessor...")
                self.processor = PriceDataProcessor(px_path=self.processor.px_path)
                self._px_mtime = current_mtime
            return self.processor

    def GetFilteredPrices(self, request, context):
        """Unary RPC: vrne filtriran seznam cen živil glede na kategorijo, leto in iskalni niz."""
        proc = self._get_fresh_processor()
        category = request.category.strip() if request.category else None
        year = request.year if request.year > 0 else None
        search = request.search.strip() if request.search else None

        logger.info(
            "gRPC GetFilteredPrices(category=%r, year=%r, search=%r, only_food=%s)",
            category, year, search, request.only_food
        )

        records = proc.get_filtered_prices(
            category=category,
            year=year,
            search=search,
            only_food=request.only_food,
        )
        resolved_year = year if year is not None else int(proc.df_clean['LETO'].max())

        items_pb = [
            anti_wolt_pb2.FoodPriceItem(
                product_raw=r["product_raw"],
                product_name=r["product_name"],
                unit=r["unit"],
                category=r["category"],
                year=int(r["year"]),
                price_eur=float(r["price_eur"]),
            )
            for r in records
        ]

        return anti_wolt_pb2.PriceListResponse(
            count=len(items_pb),
            resolved_year=resolved_year,
            items=items_pb,
        )

    def GetProductHistory(self, request, context):
        """Unary RPC: vrne zgodovino gibanja cene za izbrani izdelek (2018-2025)."""
        proc = self._get_fresh_processor()
        product_query = (request.product or "Testenine").strip()
        logger.info("gRPC GetProductHistory(product=%r)", product_query)

        data = proc.get_product_history(product_query)
        if not data.get("found"):
            return anti_wolt_pb2.ProductHistoryResponse(
                found=False,
                query=data.get("query", product_query),
            )

        return anti_wolt_pb2.ProductHistoryResponse(
            found=True,
            query=product_query,
            product_name=data["product_name"],
            unit=data["unit"],
            category=data["category"],
            years=[int(y) for y in data["years"]],
            prices=[float(p) for p in data["prices"]],
            start_year=int(data["start_year"]) if data["start_year"] is not None else 0,
            end_year=int(data["end_year"]) if data["end_year"] is not None else 0,
            start_price=float(data["start_price"]),
            end_price=float(data["end_price"]),
            diff_eur=float(data["diff_eur"]),
            diff_pct=float(data["diff_pct"]),
        )

    def GetAntiWoltComparison(self, request, context):
        """Unary RPC: vrne primerjavo stroškov domače priprave obrokov napram gostinstvu."""
        proc = self._get_fresh_processor()
        year = request.year if request.year > 0 else 2025
        logger.info("gRPC GetAntiWoltComparison(year=%d)", year)

        comp = proc.get_anti_wolt_comparison(year=year)
        comparisons_pb = [
            anti_wolt_pb2.MealComparison(
                meal=c["meal"],
                home_title=c["home_title"],
                home_cost=float(c["home_cost"]),
                restaurant_title=c["restaurant_title"],
                restaurant_cost=float(c["restaurant_cost"]),
                savings_eur=float(c["savings_eur"]),
                savings_pct=float(c["savings_pct"]),
            )
            for c in comp["comparisons"]
        ]

        return anti_wolt_pb2.ComparisonResponse(
            year=int(comp["year"]),
            comparisons=comparisons_pb,
            average_savings_pct=float(comp["average_savings_pct"]),
        )

    def GetInflationLeaders(self, request, context):
        """Unary RPC: vrne izdelke z največjo odstotno podražitvijo v izbranem obdobju."""
        proc = self._get_fresh_processor()
        start_year = request.start_year if request.start_year > 0 else 2018
        end_year = request.end_year if request.end_year > 0 else 2025
        limit = request.limit if request.limit > 0 else 8
        logger.info("gRPC GetInflationLeaders(start=%d, end=%d, limit=%d)", start_year, end_year, limit)

        items = proc.get_inflation_leaders(start_year=start_year, end_year=end_year, limit=limit)
        items_pb = [_build_inflation_item_pb(row) for row in items]

        return anti_wolt_pb2.InflationResponse(
            period=f"{start_year} - {end_year}",
            items=items_pb,
        )

    def StreamCategoryInflation(self, request, context):
        """
        Server-Side Streaming RPC: ob izbiri kategorije in časovnega obdobja sproti
        izračuna ter pošilja podatke o podražitvah posameznih živil v realnem času.
        """
        proc = self._get_fresh_processor()
        category = request.category.strip() if request.category else "Vse"
        start_year = request.start_year if request.start_year > 0 else 2018
        end_year = request.end_year if request.end_year > 0 else 2025

        if start_year >= end_year:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Začetno leto mora biti manjše od končnega leta."
            )

        # Omejimo zamik na varen interval [0, 1000] ms, da uporabniški vmesnik ostane odziven
        delay_sec = max(0, min(request.delay_ms, 1000)) / 1000.0

        logger.info(
            "gRPC StreamCategoryInflation START (category=%r, %d->%d, delay_ms=%d)",
            category, start_year, end_year, int(delay_sec * 1000)
        )

        items = proc.get_category_inflation_items(
            category=category,
            start_year=start_year,
            end_year=end_year,
        )
        total_count = len(items)
        running_sum_pct = 0.0

        for idx, row in enumerate(items, start=1):
            # Če odjemalec (ali brskalnik prek SSE) prekine povezavo, takoj ustavimo tok
            if not context.is_active():
                logger.info("gRPC StreamCategoryInflation prekinjen s strani odjemalca pri %d/%d.", idx - 1, total_count)
                break

            running_sum_pct += float(row["diff_pct"])
            running_avg_pct = round(running_sum_pct / idx, 1)

            # Prvi element pošljemo takoj, nadaljnje pa s kratkim zamikom za nazoren prikaz v realnem času
            if idx > 1 and delay_sec > 0:
                time.sleep(delay_sec)

            yield anti_wolt_pb2.CategoryInflationEvent(
                item=_build_inflation_item_pb(row),
                processed_count=idx,
                total_count=total_count,
                running_avg_pct=running_avg_pct,
            )

        logger.info("gRPC StreamCategoryInflation KONČAN (poslanih %d zapisov).", total_count)


def serve(host: str = "0.0.0.0", port: int = 50051) -> None:
    """Zažene gRPC strežnik in čaka na zahteve odjemalcev."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    anti_wolt_pb2_grpc.add_AntiWoltServiceServicer_to_server(AntiWoltServiceServicer(), server)
    bind_addr = f"{host}:{port}"
    server.add_insecure_port(bind_addr)
    server.start()

    print("\n=======================================================")
    print("  ANTI-WOLT gRPC STREŽNIK (AntiWoltService)            ")
    print(f"  Posluša na: {bind_addr} (5 RPC metod: 4 unary + 1 stream)")
    print("=======================================================\n")

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Zaustavljam Anti-Wolt gRPC strežnik...")
        server.stop(grace=2)


if __name__ == "__main__":
    grpc_host = os.getenv("GRPC_HOST", "0.0.0.0")
    grpc_port = int(os.getenv("GRPC_PORT", "50051"))
    serve(host=grpc_host, port=grpc_port)
