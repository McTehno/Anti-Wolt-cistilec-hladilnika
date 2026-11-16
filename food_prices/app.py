"""
Flask spletna aplikacija za dinamično vizualizacijo in analizo statističnih cen živil
iz portala OPSI / SiStat (SURS) v formatu PCAXIS (.px).

Aplikacija omogoča:
- Prikaz sodobne interaktivne nadzorne plošče (Dashboard) z vizualizacijo Chart.js.
- Dinamično pridobivanje podatkov prek REST API končnih točk ob nalaganju strani.
- Filtriranje cen po kategoriji, letu in ključnih besedah.
- Analizo podražitve (inflacije) po kategorijah in posameznih živilih.
- "Anti-Wolt" kalkulator prihrankov: primerjava stroškov domače priprave z gostinskimi obroki.
- Ponovno osvežitev podatkov v živo iz oddaljene baze SURS s pritiskom na gumb.
"""

import os
import sys
import json
import logging
import grpc
from flask import Flask, render_template, jsonify, request, Response, stream_with_context

# Zagotovimo delovanje tako pri `python -m food_prices.app` kot pri `python food_prices/app.py`
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from food_prices.processor import PriceDataProcessor
from food_prices.client import SiStatClient
from food_prices.ai_advisor import OpenRouterAiService, AiRecipeRecommendation
from food_prices.grpc_client import AntiWoltGrpcClient

# Konfiguracija beleženja
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Nastavitev poti za predloge (templates) in statične datoteke
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)

# Inicializacija procesorja za metapodatke/osveževanje, AI storitve ter gRPC odjemalca (stub)
processor = PriceDataProcessor()
ai_service = OpenRouterAiService()
grpc_client = AntiWoltGrpcClient()


def _format_grpc_error(err: grpc.RpcError) -> tuple[str, int]:
    """Pretvori gRPC napako v berljivo slovensko sporočilo in ustrezno HTTP statusno kodo."""
    code = err.code()
    details = err.details() or str(err)
    if code == grpc.StatusCode.UNAVAILABLE or (
        code == grpc.StatusCode.DEADLINE_EXCEEDED and not grpc_client.is_available(timeout=0.2)
    ):
        return (
            f"gRPC strežnik ({grpc_client.target}) ni dosegljiv. "
            "Zaženite ga z ukazom: python -m food_prices.grpc_server",
            503,
        )
    if code == grpc.StatusCode.DEADLINE_EXCEEDED:
        return (f"Časovna omejitev gRPC klica je potekla ({details}).", 504)
    if code == grpc.StatusCode.INVALID_ARGUMENT:
        return (details, 400)
    return (f"gRPC napaka ({code.name}): {details}", 500)


@app.route('/')
def index():
    """
    Vstopna točka: naloži glavno spletno stran z nadzorno ploščo.
    Vsi podatki in grafi se nato dinamično napolnijo prek JavaScript fetch / SSE klicev.
    """
    return render_template('index.html')


@app.route('/api/grpc-status', methods=['GET'])
def get_grpc_status():
    """REST API: Preveri, ali je povezani gRPC strežnik na portu 50051 dosegljiv."""
    available = grpc_client.is_available()
    return jsonify({
        "status": "success",
        "connected": available,
        "target": grpc_client.target,
    })


@app.route('/api/metadata', methods=['GET'])
def get_metadata():
    """
    REST API: Vrne metapodatke o tabeli, viru, letih in kategorijah.
    """
    try:
        meta = processor.get_metadata()
        return jsonify({"status": "success", "data": meta})
    except Exception as e:
        logger.error(f"Napaka pri pridobivanju metapodatkov: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/prices', methods=['GET'])
def get_prices():
    """
    REST -> gRPC (GetFilteredPrices): Vrne filtrirane cene izdelkov prek gRPC strežnika.
    Podpira poizvedbene parametre (query params):
    - category: kategorija živila (npr. '01.1.2 MESO' ali 'Vse')
    - year: leto (npr. 2025)
    - search: iskalni niz v imenu izdelka
    """
    try:
        category = request.args.get('category', None)
        year_str = request.args.get('year', None)
        search = request.args.get('search', None)

        year = int(year_str) if year_str and year_str.isdigit() else None

        result = grpc_client.get_filtered_prices(
            category=category,
            year=year,
            search=search,
            only_food=True
        )

        return jsonify({
            "status": "success",
            "transport": "gRPC",
            "count": result["count"],
            "filters": {
                "category": category,
                "year": result["resolved_year"],
                "search": search
            },
            "data": result["data"]
        })
    except grpc.RpcError as err:
        msg, status_code = _format_grpc_error(err)
        logger.error(f"gRPC napaka pri /api/prices: {msg}")
        return jsonify({"status": "error", "message": msg}), status_code
    except Exception as e:
        logger.error(f"Napaka pri pridobivanju cen: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/history', methods=['GET'])
def get_history():
    """
    REST -> gRPC (GetProductHistory): Vrne časovno vrsto gibanja cen (2018-2025) za izbrani izdelek.
    Parameter:
    - product: ime izdelka (npr. 'Riž, beli' ali 'Testenine')
    """
    try:
        product_query = request.args.get('product', 'Testenine')
        history_data = grpc_client.get_product_history(product_query)
        return jsonify({"status": "success", "transport": "gRPC", "data": history_data})
    except grpc.RpcError as err:
        msg, status_code = _format_grpc_error(err)
        logger.error(f"gRPC napaka pri /api/history: {msg}")
        return jsonify({"status": "error", "message": msg}), status_code
    except Exception as e:
        logger.error(f"Napaka pri pridobivanju zgodovine za '{request.args.get('product')}': {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/categories-trend', methods=['GET'])
def get_categories_trend():
    """
    REST API: Vrne povprečne cene po skupinah živil skozi vsa razpoložljiva leta.
    Uporabno za primerjalne grafe rasti cen po skupinah.
    """
    try:
        trend_data = processor.get_category_averages_by_year()
        return jsonify({"status": "success", "data": trend_data})
    except Exception as e:
        logger.error(f"Napaka pri pridobivanju trendov kategorij: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/inflation', methods=['GET'])
def get_inflation():
    """
    REST -> gRPC (GetInflationLeaders): Vrne izdelke z največjo odstotno podražitvijo.
    Parametra:
    - start_year (privzeto 2018)
    - end_year (privzeto 2025)
    - limit (privzeto 8)
    """
    try:
        start_year = int(request.args.get('start_year', 2018))
        end_year = int(request.args.get('end_year', 2025))
        limit = int(request.args.get('limit', 8))

        inflation_res = grpc_client.get_inflation_leaders(start_year, end_year, limit)
        return jsonify({
            "status": "success",
            "transport": "gRPC",
            "period": inflation_res["period"],
            "data": inflation_res["data"]
        })
    except grpc.RpcError as err:
        msg, status_code = _format_grpc_error(err)
        logger.error(f"gRPC napaka pri /api/inflation: {msg}")
        return jsonify({"status": "error", "message": msg}), status_code
    except Exception as e:
        logger.error(f"Napaka pri izračunu inflacije: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/comparison', methods=['GET'])
def get_comparison():
    """
    REST -> gRPC (GetAntiWoltComparison): Vrne primerjavo cen za koncept Anti-Wolt.
    Parameter:
    - year (privzeto 2025)
    """
    try:
        year_str = request.args.get('year', '2025')
        year = int(year_str) if year_str.isdigit() else 2025
        comparison_data = grpc_client.get_anti_wolt_comparison(year)
        return jsonify({"status": "success", "transport": "gRPC", "data": comparison_data})
    except grpc.RpcError as err:
        msg, status_code = _format_grpc_error(err)
        logger.error(f"gRPC napaka pri /api/comparison: {msg}")
        return jsonify({"status": "error", "message": msg}), status_code
    except Exception as e:
        logger.error(f"Napaka pri pridobivanju Anti-Wolt primerjave: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/stream/category-inflation', methods=['GET'])
def stream_category_inflation_sse():
    """
    SSE most za gRPC Server-Side Streaming (`StreamCategoryInflation`).
    Brskalnik se poveže prek `EventSource`, Flask pa sproti bere binarni gRPC tok
    in vsak prejeti izdelek takoj odda kot `text/event-stream` dogodek v realnem času.
    """
    category = request.args.get('category', 'Vse')
    try:
        start_year = int(request.args.get('start_year', 2018))
        end_year = int(request.args.get('end_year', 2025))
        delay_ms = int(request.args.get('delay_ms', 120))
    except ValueError:
        return jsonify({"status": "error", "message": "Letnici in zamik morata biti celi števili."}), 400

    def event_generator():
        try:
            for event_payload in grpc_client.stream_category_inflation(
                category=category,
                start_year=start_year,
                end_year=end_year,
                delay_ms=delay_ms,
            ):
                yield f"data: {json.dumps(event_payload, ensure_ascii=False)}\n\n"
            # Zaključni dogodek prepreči samodejno ponovno povezovanje v brskalniku
            yield f"event: done\ndata: {json.dumps({'completed': True})}\n\n"
        except grpc.RpcError as err:
            msg, _ = _format_grpc_error(err)
            logger.error(f"gRPC stream napaka: {msg}")
            yield f"event: stream_error\ndata: {json.dumps({'message': msg}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.error(f"Nepričakovana napaka med pretakanjem SSE: {exc}")
            yield f"event: stream_error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(event_generator()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@app.route('/api/refresh', methods=['POST'])
def refresh_data():
    """
    REST API: Sproži ponoven prenos PCAXIS datoteke iz SiStat/OPSI strežnika
    ter ponovno inicializira procesor.
    """
    global processor
    try:
        client = SiStatClient()
        updated_path = client.fetch_dataset(force_refresh=True)
        # Ponovno naložimo podatke v procesor
        processor = PriceDataProcessor(px_path=updated_path)
        return jsonify({
            "status": "success",
            "message": "Podatki uspešno osveženi iz oddaljenega vira SiStat / OPSI.",
            "file": updated_path
        })
    except Exception as e:
        logger.error(f"Napaka pri osveževanju podatkov: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/ai-recipe', methods=['POST'])
def generate_ai_recipe():
    """
    REST API: Dinamično generira recept iz ostankov v hladilniku z uporabo LLM prek OpenRouterja.
    
    Vhodni JSON parametri (prejeti iz spletnega obrazca):
    - ingredients: Vnesene sestavine uporabnika (npr. 'star kruh, jajca, sir, paradižnik')
    - max_time: Razpoložljiv čas priprave v minutah (npr. 15, 30 ali prazno)
    - servings: Število oseb / porcij (privzeto 2)
    - meal_type: Tip obroka (Zajtrk, Hitro kosilo, Topla večerja, Prigrizek...)
    - diet: Prehranske posebnosti (Brez omejitev, Vegetarijansko, Vegansko...)
    - client_context: Dinamični kontekst iz brskalnika (trenutni čas, dan v tednu)
    
    Izhod:
    - JSON objekt z deserializiranimi podatki instance AiRecipeRecommendation
    """
    try:
        payload = request.get_json() or {}
        ingredients = payload.get('ingredients', '').strip()

        if not ingredients:
            return jsonify({
                "status": "error",
                "message": "Prosimo vnesite vsaj eno sestavino, ki jo imate na voljo v hladilniku ali shrambi."
            }), 400

        # Čiščenje in pretvorba časovnih ter količinskih parametrov
        max_time_raw = payload.get('max_time')
        max_time = int(max_time_raw) if max_time_raw and str(max_time_raw).isdigit() else None

        servings_raw = payload.get('servings', 2)
        servings = int(servings_raw) if str(servings_raw).isdigit() else 2

        meal_type = payload.get('meal_type', 'Vseeno')
        diet = payload.get('diet', 'Brez omejitev')
        client_context = payload.get('client_context', '')

        # Klic AI storitve: generira prompt, pošlje na OpenRouter, prejme JSON ter
        # deserializira odgovor v Python razred AiRecipeRecommendation
        recipe_obj: AiRecipeRecommendation = ai_service.generate_recipe(
            ingredients=ingredients,
            max_time_min=max_time,
            servings=servings,
            meal_type=meal_type,
            diet_preference=diet,
            client_context=client_context
        )

        return jsonify({
            "status": "success",
            "data": recipe_obj.to_dict()
        })
    except ValueError as ve:
        logger.warning(f"Validacijska napaka pri AI klicu: {ve}")
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        logger.error(f"Napaka pri klicu AI modela: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"Napaka pri pridobivanju AI recepta: {str(e)}"
        }), 500



if __name__ == '__main__':
    # Zagon lokalnega razvojnega strežnika z nastavitvami iz .env
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "True").lower() in ("true", "1")

    print("\n=======================================================")
    print("  ANTI-WOLT: Nadzorna plošča cen živil (OPSI / SiStat) ")
    print(f"  Strežnik teče na: http://{host}:{port}               ")
    print("=======================================================\n")
    app.run(host=host, port=port, debug=debug)

