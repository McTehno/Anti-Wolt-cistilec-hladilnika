"""
Modul za razčlenjevanje (parsing), čiščenje, filtriranje in agregacijo statističnih podatkov
iz oblike PCAXIS (.px) z uporabo knjižnice pyaxis.

Ta modul pretvori surove statistične podatke SiStat/OPSI v obogatene relacijske strukture:
- Določi pripadnost živil posameznim prehranskim in storitvenim kategorijam (COICOP).
- Loči ime izdelka od merske enote (npr. kg, l, porcija).
- Izračunava časovne serije, stopnje rasti (inflacijo) in agregirane povprečne cene.
- Pripravlja primerjalne analize "Anti-Wolt" (strošek domačih surovin napram restavracijski hrani).
"""

import os
import re
import logging
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
from pyaxis import pyaxis

from .client import SiStatClient, DEFAULT_PX_PATH

logger = logging.getLogger(__name__)


class PriceDataProcessor:
    """
    Procesni razred za analizo in obdelavo maloprodajnih cen iz datoteke PCAXIS (.px).
    """

    def __init__(self, px_path: Optional[str] = None):
        """
        Inicializacija procesorja podatkov.

        :param px_path: Pot do .px datoteke. Če ni podana, se uporabi privzeta pot
                        ali pa se datoteka avtomatsko prenese prek SiStatClient-a.
        """
        self.px_path = px_path or DEFAULT_PX_PATH

        # Če datoteka na disku še ne obstaja, sprožimo prenos preko odjemalca
        if not os.path.exists(self.px_path):
            logger.info("Lokalna .px datoteka ne obstaja, sprožam prenos ...")
            client = SiStatClient(cache_path=self.px_path)
            self.px_path = client.fetch_dataset()

        self.metadata: Dict[str, Any] = {}
        self.df_raw: Optional[pd.DataFrame] = None
        self.df_clean: Optional[pd.DataFrame] = None
        self.categories: Dict[str, List[str]] = {}

        # Naložimo in pripravimo podatke
        self._load_and_process()

    def _load_and_process(self) -> None:
        """
        Glavna interna metoda:
        1. Razčleni .px datoteko s pomočjo knjižnice pyaxis.
        2. Izvleče metapodatke (naslov, vir, čas posodobitve).
        3. Rekonstruira hierarhijo kategorij (COICOP) glede na zaporedje v PCAXIS.
        4. Očisti imena izdelkov ter izlušči merske enote.
        5. Pretovri stolpec cen v numerično obliko (float).
        """
        logger.info(f"Razčlenjujem PCAXIS datoteko s pyaxis: {self.px_path}")
        
        # Knjižnica pyaxis razbere tako metapodatke kot matriko vrednosti v Pandas DataFrame.
        # Kodiranje windows-1250 / cp1250 je standardno za SiStat baze SURS.
        parsed = pyaxis.parse(self.px_path, encoding='cp1250')

        self.metadata = parsed.get('METADATA', {})
        self.df_raw = parsed.get('DATA')

        if self.df_raw is None or self.df_raw.empty:
            raise ValueError("Knjižnica pyaxis ni uspela pridobiti podatkovne tabele (DATA) iz .px datoteke.")

        # Pridobimo seznam unikatnih postavk v vrstnem redu, kot so zapisane v .px datoteki
        raw_items = self.df_raw['IZDELKI IN STORITVE'].unique()

        # Rekonstrukcija kategorij:
        # V tabeli SiStat so kategorije zapisane kot samostojni nizi (npr. '01.1.1 KRUH IN IZDELKI IZ ŽIT'),
        # posamezni izdelki pa se začnejo s tremi pikami '...' (npr. '...Riž, beli (kg)').
        item_to_category: Dict[str, str] = {}
        current_category = "Ostalo"

        for item in raw_items:
            item_str = str(item).strip()
            if not item_str.startswith("..."):
                # Gre za naslov kategorije
                current_category = item_str
                if current_category not in self.categories:
                    self.categories[current_category] = []
            else:
                # Gre za konkreten izdelek ali storitev
                self.categories[current_category].append(item_str)
                item_to_category[item_str] = current_category

        # Filtriramo samo vrstice z dejanskimi izdelki (tiste z '...'), saj kategorije same nimajo cen
        df_products = self.df_raw[self.df_raw['IZDELKI IN STORITVE'].str.startswith("...", na=False)].copy()

        # Dodamo stolpec kategorije
        df_products['KATEGORIJA'] = df_products['IZDELKI IN STORITVE'].map(item_to_category)

        # Čiščenje imena in izluščenje merske enote:
        # Primer: '...Mleko, trajno, polnomastno (l)' -> Ime: 'Mleko, trajno, polnomastno', Enota: 'l'
        def extract_name_and_unit(raw_name: str) -> Tuple[str, str]:
            cleaned = raw_name.lstrip(".").strip()
            # Poišči zadnji oklepaj na koncu niza
            match = re.search(r'^(.*?)\s*\(([^()]+)\)$', cleaned)
            if match:
                return match.group(1).strip(), match.group(2).strip()
            return cleaned, "kos"

        extracted = df_products['IZDELKI IN STORITVE'].apply(extract_name_and_unit)
        df_products['IZDELEK_IME'] = extracted.apply(lambda x: x[0])
        df_products['ENOTA'] = extracted.apply(lambda x: x[1])

        # Pretvorba cen v numerični tip
        df_products['CENA_EUR'] = pd.to_numeric(df_products['DATA'], errors='coerce')
        df_products['LETO'] = pd.to_numeric(df_products['LETO'], errors='coerce').astype(int)

        # Odstranimo vrstice, kjer cena manjka (NaN)
        self.df_clean = df_products.dropna(subset=['CENA_EUR']).copy()
        logger.info(f"Podatki očiščeni. Veljavnih zapisov cen: {len(self.df_clean)}")

    def get_metadata(self) -> Dict[str, Any]:
        """
        Vrne metapodatke tabele in seznam razpoložljivih dimenzij.
        """
        title = self.metadata.get('TITLE', ['Povprečne drobnoprodajne cene živil in storitev'])
        if isinstance(title, list):
            title = title[0]

        years = sorted(self.df_clean['LETO'].unique().tolist())
        categories_list = list(self.categories.keys())

        # Prijazna imena za najpomembnejše prehranske kategorije
        food_categories = [c for c in categories_list if any(prefix in c for prefix in ['01.1', '01.2', '02.1', '11.1'])]

        return {
            "title": title,
            "source": "Statistični urad Republike Slovenije (SURS) / OPSI",
            "matrix": self.metadata.get('MATRIX', '0411005S'),
            "updated": self.metadata.get('CREATION-DATE', '2026'),
            "years": years,
            "all_categories": categories_list,
            "food_categories": food_categories,
            "total_records": len(self.df_clean),
            "distinct_products": self.df_clean['IZDELEK_IME'].nunique()
        }

    def get_filtered_prices(
        self,
        category: Optional[str] = None,
        year: Optional[int] = None,
        search: Optional[str] = None,
        only_food: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Filtrira cene po kategoriji, letu in iskalnem nizu.

        :param category: Točno ime kategorije ali del imena (neobvezno).
        :param year: Izbrano leto (neobvezno, privzeto zadnje razpoložljivo leto).
        :param search: Iskalni niz za filtriranje po imenu izdelka.
        :param only_food: Če je True, privzeto vključi samo prehrano in pijače (01.x, 02.x, 11.x).
        :return: Seznam slovarjev s filtriranimi podatki.
        """
        df = self.df_clean.copy()

        # Filtriranje po letu
        if year is not None:
            df = df[df['LETO'] == year]
        else:
            # Privzeto vzamemo zadnje leto v podatkih
            latest_year = df['LETO'].max()
            df = df[df['LETO'] == latest_year]

        # Omejitev na prehranske kategorije, če je zahtevano
        if only_food and not category:
            df = df[df['KATEGORIJA'].str.contains(r'^(?:01\.|02\.1|11\.1)', regex=True, na=False)]

        # Filtriranje po kategoriji
        if category and category != "Vse":
            df = df[df['KATEGORIJA'].str.contains(re.escape(category), case=False, na=False)]

        # Filtriranje po iskalnem nizu (tekstovni filter)
        if search:
            search_pattern = re.escape(search.strip())
            df = df[df['IZDELEK_IME'].str.contains(search_pattern, case=False, na=False)]

        # Sortiranje po imenu izdelka
        df = df.sort_values(by=['IZDELEK_IME'])

        results = []
        for _, row in df.iterrows():
            results.append({
                "product_raw": row['IZDELKI IN STORITVE'],
                "product_name": row['IZDELEK_IME'],
                "unit": row['ENOTA'],
                "category": row['KATEGORIJA'],
                "year": int(row['LETO']),
                "price_eur": round(float(row['CENA_EUR']), 2)
            })

        return results

    def get_product_history(self, product_query: str) -> Dict[str, Any]:
        """
        Vrne časovno serijo gibanja cen za specifičen izdelek od 2018 do 2025.

        :param product_query: Delno ali celotno ime izdelka.
        :return: Slovar z zgodovino cen, absolutno in odstotno spremembo.
        """
        # Poišči ujemanje med izdelki
        df_matching = self.df_clean[self.df_clean['IZDELEK_IME'].str.contains(re.escape(product_query), case=False, na=False)]
        if df_matching.empty:
            return {"found": False, "query": product_query, "history": []}

        # Vzamemo prvi zadetek
        best_match_name = df_matching['IZDELEK_IME'].iloc[0]
        df_item = self.df_clean[self.df_clean['IZDELEK_IME'] == best_match_name].sort_values(by='LETO')

        years = df_item['LETO'].tolist()
        prices = [round(float(p), 2) for p in df_item['CENA_EUR'].tolist()]
        unit = df_item['ENOTA'].iloc[0]
        category = df_item['KATEGORIJA'].iloc[0]

        start_price = prices[0] if prices else 0.0
        end_price = prices[-1] if prices else 0.0
        diff_eur = round(end_price - start_price, 2)
        diff_pct = round(((end_price - start_price) / start_price) * 100, 1) if start_price > 0 else 0.0

        return {
            "found": True,
            "product_name": best_match_name,
            "unit": unit,
            "category": category,
            "years": years,
            "prices": prices,
            "start_year": years[0] if years else None,
            "end_year": years[-1] if years else None,
            "start_price": start_price,
            "end_price": end_price,
            "diff_eur": diff_eur,
            "diff_pct": diff_pct
        }

    def get_category_averages_by_year(self) -> Dict[str, Any]:
        """
        Agregacija: Izračuna povprečno ceno košarice po posameznih prehranskih kategorijah skozi leta.
        Uporabno za prikaz linijskega ali večstolpčnega grafa dinamike cen.
        """
        df_food = self.df_clean[self.df_clean['KATEGORIJA'].str.contains(r'^(?:01\.1|01\.2|11\.1)', regex=True, na=False)].copy()

        # Prijaznejše krajše poimenovanje kategorij
        short_names = {
            '01.1.1 KRUH IN IZDELKI IZ ŽIT': 'Žita in kruh',
            '01.1.2 MESO': 'Meso',
            '01.1.4 MLEKO, SIR IN JAJCA': 'Mleko, sir, jajca',
            '01.1.5 OLJE IN MAŠČOBE': 'Olja in maščobe',
            '01.1.6 SADJE': 'Sadje',
            '01.1.7 ZELENJAVA': 'Zelenjava',
            '01.1.8 SLADKOR, MARMELADA, MED, ČOKOLADA IN KONDITORSKI IZDELKI': 'Sladkor in slaščice',
            '01.1.9 DRUGI PREHRAMBNI IZDELKI, D.N.': 'Drugo',
            '01.2 BREZALKOHOLNE PIJAČE': 'Pijače',
            '11.1 GOSTINSKE STORITVE': 'Gostinske storitve'
        }

        df_food['KRATKO_IME'] = df_food['KATEGORIJA'].map(lambda c: short_names.get(c, c))

        # Pivot tabela: vrstice so kategorije, stolpci so leta, vrednosti povprečje cen
        pivot = df_food.pivot_table(index='KRATKO_IME', columns='LETO', values='CENA_EUR', aggfunc='mean')
        
        years = [int(col) for col in pivot.columns]
        series = []
        for cat_name, row in pivot.iterrows():
            series.append({
                "category": cat_name,
                "values": [round(float(val), 2) if pd.notnull(val) else None for val in row]
            })

        return {
            "years": years,
            "series": series
        }

    def get_inflation_leaders(self, start_year: int = 2018, end_year: int = 2025, limit: int = 8) -> List[Dict[str, Any]]:
        """
        Agregacija in razvrščanje: Poišče izdelke z največjo odstotno podražitvijo (rekorderje inflacije)
        med izbranima letoma.
        """
        df_start = self.df_clean[self.df_clean['LETO'] == start_year][['IZDELEK_IME', 'ENOTA', 'KATEGORIJA', 'CENA_EUR']]
        df_end = self.df_clean[self.df_clean['LETO'] == end_year][['IZDELEK_IME', 'CENA_EUR']]

        merged = pd.merge(df_start, df_end, on='IZDELEK_IME', suffixes=(f'_{start_year}', f'_{end_year}'))
        
        # Omejimo se na prehranske izdelke in gostinstvo
        merged = merged[merged['KATEGORIJA'].str.contains(r'^(?:01\.|02\.1|11\.1)', regex=True, na=False)]

        col_start = f'CENA_EUR_{start_year}'
        col_end = f'CENA_EUR_{end_year}'

        merged['diff_eur'] = merged[col_end] - merged[col_start]
        merged['diff_pct'] = ((merged[col_end] - merged[col_start]) / merged[col_start]) * 100

        top_risers = merged.sort_values(by='diff_pct', ascending=False).head(limit)

        result = []
        for _, row in top_risers.iterrows():
            result.append({
                "product_name": row['IZDELEK_IME'],
                "unit": row['ENOTA'],
                "category": row['KATEGORIJA'],
                "start_price": round(float(row[col_start]), 2),
                "end_price": round(float(row[col_end]), 2),
                "diff_eur": round(float(row['diff_eur']), 2),
                "diff_pct": round(float(row['diff_pct']), 1)
            })

        return result

    def get_anti_wolt_comparison(self, year: int = 2025) -> Dict[str, Any]:
        """
        Specializirana analiza za domeno 'Anti-Wolt':
        Primerja ceno domače priprave obroka (osnovne surovine) s primerljivo gostinsko jedjo / dostavo
        iz iste SiStat tabele.
        
        Primeri parov:
        - Testenine doma (1 porcija ~ 200g testenin + omaka) vs. Testenine z omako v restavraciji
        - Pica doma (moka, kvas, sir, paradižnik) vs. Velika pica v restavraciji
        - Domača kava (1 skodelica) vs. Espresso v lokalu
        - Kosilo / Meni doma (meso, priloga, solata) vs. Meni kosilo v gostinstvu
        """
        # Pridobimo cene za izbrano leto
        df_y = self.df_clean[self.df_clean['LETO'] == year].copy()
        prices_dict = dict(zip(df_y['IZDELEK_IME'], df_y['CENA_EUR']))

        comparisons = []

        # 1. TESTENINE
        # Cena 1 kg testenin v trgovini:
        pasta_raw_kg = prices_dict.get('Testenine', 3.25)
        # 1 porcija domačih testenin je cca 200g (0.2 * cena kg) + ocenjeno 0.80 EUR za omako/paradižnik/začimbe
        pasta_home_portion = round((pasta_raw_kg * 0.20) + 0.80, 2)
        pasta_restaurant = prices_dict.get('Testenine z omako', 8.50)
        comparisons.append({
            "meal": "Testenine z omako",
            "home_title": "Domača priprava (200g testenin + paradižnikova omaka)",
            "home_cost": pasta_home_portion,
            "restaurant_title": "Gostinska porcija / dostava",
            "restaurant_cost": round(pasta_restaurant, 2),
            "savings_eur": round(pasta_restaurant - pasta_home_portion, 2),
            "savings_pct": round(((pasta_restaurant - pasta_home_portion) / pasta_restaurant) * 100, 1)
        })

        # 2. PICA
        flour_kg = prices_dict.get('Moka, pšenična, razen polnozrnata', 0.80)
        cheese_kg = prices_dict.get('Sir', 10.67)
        # Sestavine za 1 domačo pico: 250g moke (~0.20€) + 150g sira (~1.60€) + paradižnik/šunka (~1.20€) = ~3.00€
        pizza_home = round((flour_kg * 0.25) + (cheese_kg * 0.15) + 1.20, 2)
        pizza_restaurant = prices_dict.get('Pica, velika', 10.50)
        comparisons.append({
            "meal": "Pica",
            "home_title": "Domača pica (moka, sir, paradižnik, nadevi)",
            "home_cost": pizza_home,
            "restaurant_title": "Velika pica v piceriji / dostava",
            "restaurant_cost": round(pizza_restaurant, 2),
            "savings_eur": round(pizza_restaurant - pizza_home, 2),
            "savings_pct": round(((pizza_restaurant - pizza_home) / pizza_restaurant) * 100, 1)
        })

        # 3. KAVA
        coffee_kg = prices_dict.get('Kava, klasična', 12.50)
        # 1 kg kave zadostuje za približno 140 skodelic -> cena na skodelico doma:
        coffee_home_cup = round(coffee_kg / 140.0, 2)
        coffee_bar = prices_dict.get('Črna kava, espresso', 1.60)
        comparisons.append({
            "meal": "Kava / Espresso",
            "home_title": "Domača kava (1 skodelica iz 7g kave)",
            "home_cost": coffee_home_cup,
            "restaurant_title": "Espresso v gostinskem lokalu",
            "restaurant_cost": round(coffee_bar, 2),
            "savings_eur": round(coffee_bar - coffee_home_cup, 2),
            "savings_pct": round(((coffee_bar - coffee_home_cup) / coffee_bar) * 100, 1)
        })

        # 4. KOSILO (GLAVNA JED Z MESOM IN PRILOGO)
        chicken_kg = prices_dict.get('Meso, piščančje brez kosti', 8.51)
        rice_kg = prices_dict.get('Riž, beli', 2.57)
        # Domače kosilo: 250g piščanca (~2.13€) + 100g riža (~0.26€) + solata/olje (~0.80€) = ~3.20€
        lunch_home = round((chicken_kg * 0.25) + (rice_kg * 0.10) + 0.80, 2)
        lunch_restaurant = prices_dict.get('Meni, kosilo', 11.80)
        comparisons.append({
            "meal": "Dnevno kosilo (mesna jed s prilogo)",
            "home_title": "Doma pripravljeno kosilo (piščančji file, riž, solata)",
            "home_cost": lunch_home,
            "restaurant_title": "Meni kosilo v restavraciji",
            "restaurant_cost": round(lunch_restaurant, 2),
            "savings_eur": round(lunch_restaurant - lunch_home, 2),
            "savings_pct": round(((lunch_restaurant - lunch_home) / lunch_restaurant) * 100, 1)
        })

        # Skupni povprečni prihranek
        avg_savings_pct = round(sum(c['savings_pct'] for c in comparisons) / len(comparisons), 1)

        return {
            "year": year,
            "comparisons": comparisons,
            "average_savings_pct": avg_savings_pct
        }


# Hitri test delovanja procesorja ob neposrednem zagonu
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    processor = PriceDataProcessor()
    meta = processor.get_metadata()
    print("Naslov:", meta['title'])
    print("Prehranske kategorije:", len(meta['food_categories']))
    
    comp = processor.get_anti_wolt_comparison(2025)
    print("\nAnti-Wolt primerjava 2025:")
    for c in comp['comparisons']:
        print(f" - {c['meal']}: Doma {c['home_cost']}€ vs Restavracija {c['restaurant_cost']}€ -> Prihranek: {c['savings_eur']}€ ({c['savings_pct']}%)")
