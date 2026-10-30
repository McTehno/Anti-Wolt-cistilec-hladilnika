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

# Inicializacija globalnega procesorja podatkov
processor = PriceDataProcessor()


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


if __name__ == '__main__':
    # Zagon lokalnega razvojnega strežnika
    print("\n=======================================================")
    print("  ANTI-WOLT: Nadzorna plošča cen živil (OPSI / SiStat) ")
    print("  Strežnik teče na: http://127.0.0.1:5000               ")
    print("=======================================================\n")
    app.run(host='127.0.0.1', port=5000, debug=True)

