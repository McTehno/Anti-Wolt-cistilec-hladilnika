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
import logging
from flask import Flask, render_template, jsonify, request

from .processor import PriceDataProcessor
from .client import SiStatClient
from .ai_advisor import OpenRouterAiService, AiRecipeRecommendation

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

# Inicializacija globalnega procesorja podatkov in AI svetovalne storitve
processor = PriceDataProcessor()
ai_service = OpenRouterAiService()


@app.route('/')
def index():
    """
    Vstopna točka: naloži glavno spletno stran z nadzorno ploščo.
    Vsi podatki in grafi se nato dinamično napolnijo prek JavaScript fetch klicev.
    """
    return render_template('index.html')


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
    REST API: Vrne filtrirane cene izdelkov.
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

        records = processor.get_filtered_prices(
            category=category,
            year=year,
            search=search,
            only_food=True
        )

        return jsonify({
            "status": "success",
            "count": len(records),
            "filters": {
                "category": category,
                "year": year or processor.df_clean['LETO'].max(),
                "search": search
            },
            "data": records
        })
    except Exception as e:
        logger.error(f"Napaka pri pridobivanju cen: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/history', methods=['GET'])
def get_history():
    """
    REST API: Vrne časovno vrsto gibanja cen (2018-2025) za izbrani izdelek.
    Parameter:
    - product: ime izdelka (npr. 'Riž, beli' ali 'Testenine')
    """
    try:
        product_query = request.args.get('product', 'Testenine')
        history_data = processor.get_product_history(product_query)
        return jsonify({"status": "success", "data": history_data})
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
    REST API: Vrne izdelke z največjo odstotno podražitvijo.
    Parametra:
    - start_year (privzeto 2018)
    - end_year (privzeto 2025)
    - limit (privzeto 8)
    """
    try:
        start_year = int(request.args.get('start_year', 2018))
        end_year = int(request.args.get('end_year', 2025))
        limit = int(request.args.get('limit', 8))

        inflation_data = processor.get_inflation_leaders(start_year, end_year, limit)
        return jsonify({
            "status": "success",
            "period": f"{start_year} - {end_year}",
            "data": inflation_data
        })
    except Exception as e:
        logger.error(f"Napaka pri izračunu inflacije: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/comparison', methods=['GET'])
def get_comparison():
    """
    REST API: Vrne primerjavo cen za koncept Anti-Wolt (domača kuhinja vs. dostava/gostinstvo).
    Parameter:
    - year (privzeto 2025)
    """
    try:
        year_str = request.args.get('year', '2025')
        year = int(year_str) if year_str.isdigit() else 2025
        comparison_data = processor.get_anti_wolt_comparison(year)
        return jsonify({"status": "success", "data": comparison_data})
    except Exception as e:
        logger.error(f"Napaka pri pridobivanju Anti-Wolt primerjave: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


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

