"""
Glavni izvajalni modul za spletno strganje (scraping) kulinaričnih receptov.

Ta skripta krmili:
1. Povezovanje na spletno mesto Kulinarika.net prek seje `requests.Session`.
2. Postopno stranjenje (paginacijo) skozi sezname receptov ali izbrane kategorije.
3. Spoštljivo časovno zamikanje (delay) med zahtevki, da ne preobremenimo strežnika.
4. Razčlenjevanje strani v podatkovne modele prek modula `parser.py`.
5. Shranjevanje zbranih podatkov v JSON (in opcijsko XML) datoteko za nadaljnjo
   uporabo v projektu Anti-Wolt (iskanje receptov glede na vsebino hladilnika).
"""

import os
import sys
import time
import json
import argparse
import xml.etree.ElementTree as ET
from typing import List, Optional
import requests

# Zagotovitev pravilnega UTF-8 izpisa na Windows konzolah
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Zagotovitev, da se modul lahko zažene tako neposredno kot znotraj paketa
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

try:
    from .models import Recipe
    from .parser import extract_recipe_links, parse_recipe_page
except (ImportError, ValueError):
    from models import Recipe
    from parser import extract_recipe_links, parse_recipe_page


class RecipeScraper:
    """
    Razred za avtomatizirano zbiranje receptov iz spletnega mesta Kulinarika.net.
    """

    BASE_URL = "https://www.kulinarika.net"

    # Seznam priljubljenih kategorij, ki so najbolj uporabne za čistilec hladilnika
    AVAILABLE_CATEGORIES = {
        "vse": "/recepti/seznam/",
        "testenine": "/recepti/seznam/testenine/",
        "zelenjava": "/recepti/seznam/zelenjavne-jedi/",
        "meso": "/recepti/seznam/mesne-jedi/",
        "enoloncnice": "/recepti/seznam/enloncnice/",
        "jajca": "/recepti/seznam/jajcne-jedi/",
        "juhe": "/recepti/seznam/juhe-in-zakuhe/",
        "solate": "/recepti/seznam/solate/",
        "sladice": "/recepti/seznam/sladice/",
        "morska-hrana": "/recepti/seznam/morska-hrana/"
    }

    def __init__(self, delay_sec: float = 0.5):
        """
        Inicializacija strgalnika.

        Argumenti:
            delay_sec: Čas premora (v sekundah) med posameznimi HTTP zahtevki.
        """
        self.delay_sec = max(0.1, delay_sec)
        self.session = requests.Session()

        # Kulinarika.net strežnik (WAF) ob dolgih Chrome/WebKit User-Agentih vrne kodo 403.
        # Empirično testirano: preprost brskalniški identifikator 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        # deluje nemoteno in zanesljivo vrača HTTP 200 OK.
        self.session.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

    def _fetch_html(self, url: str) -> Optional[str]:
        """
        Pomožna metoda za prenos HTML vsebine s spletnega naslova.
        Poskrbi za pravilen UTF-8 dekodirnik in lovljenje morebitnih napak.
        """
        try:
            response = self.session.get(url, timeout=12)
            if response.status_code == 200:
                # Nastavimo eksplicitno kodiranje UTF-8, da se izognemo težavam s šumniki
                response.encoding = "utf-8"
                return response.text
            else:
                print(f"[OPOZORILO] Napaka pri klicu {url}: HTTP {response.status_code}")
                return None
        except requests.RequestException as e:
            print(f"[NAPAKA] Mrežna napaka pri {url}: {e}")
            return None

    def scrape_recipe(self, recipe_url: str) -> Optional[Recipe]:
        """
        Prenese in razčleni posamezen recept iz podanega URL-ja.
        """
        html = self._fetch_html(recipe_url)
        if not html:
            return None
        return parse_recipe_page(html, recipe_url)

    def scrape(
        self,
        category: str = "vse",
        limit: int = 30,
        page_size: int = 20
    ) -> List[Recipe]:
        """
        Glavna metoda za masovno zbiranje receptov skozi paginacijo.

        Argumenti:
            category: Ključ kategorije (npr. 'vse', 'testenine', 'enoloncnice' ...)
            limit: Največje število receptov, ki jih želimo zbrati (-1 za neomejeno).
            page_size: Število receptov na posamezni strani seznama na Kulinariki (privzeto 20).

        Vrača:
            Seznam razčlenjenih objektov Recipe.
        """
        category_path = self.AVAILABLE_CATEGORIES.get(category.lower(), "/recepti/seznam/")
        recipes: List[Recipe] = []
        collected_links = set()
        offset = 0

        print(f"\n==================================================================")
        print(f"Začenjam zbiranje receptov za kategorijo: '{category}'")
        print(f"Ciljno število receptov: {limit if limit > 0 else 'Vsi razpoložljivi'}")
        print(f"Časovni zamik med zahtevki: {self.delay_sec} s")
        print(f"==================================================================\n")

        # 1. Korak: Zbiranje povezav skozi strani seznama
        links_to_scrape: List[str] = []
        while True:
            # Sestavimo URL za paginacijo: npr. https://www.kulinarika.net/recepti/seznam/?offset=20
            page_url = f"{self.BASE_URL}{category_path}?offset={offset}"
            print(f"-> Nalagam seznam s strani (offset {offset}): {page_url}")

            html = self._fetch_html(page_url)
            if not html:
                break

            new_links = extract_recipe_links(html, self.BASE_URL)
            if not new_links:
                print("Ni več novih receptov na seznamu.")
                break

            added_count = 0
            for link in new_links:
                if link not in collected_links:
                    collected_links.add(link)
                    links_to_scrape.append(link)
                    added_count += 1
                    if limit > 0 and len(links_to_scrape) >= limit:
                        break

            print(f"   Najdenih {len(new_links)} povezav, novih unikatnih: {added_count} (skupaj v čakalni vrsti: {len(links_to_scrape)})")

            if (limit > 0 and len(links_to_scrape) >= limit) or added_count == 0:
                break

            offset += page_size
            time.sleep(self.delay_sec)

        # 2. Korak: Prenašanje in razčlenjevanje posameznih receptov
        total_targets = len(links_to_scrape)
        print(f"\nPričenjam podrobno razčlenjevanje {total_targets} receptov...\n")

        for idx, link in enumerate(links_to_scrape, 1):
            time.sleep(self.delay_sec)
            recipe = self.scrape_recipe(link)

            if recipe:
                recipes.append(recipe)
                ing_cnt = len(recipe.ingredients)
                print(f"[{idx}/{total_targets}] ✓ {recipe.title[:40]:<40} | Sestavin: {ing_cnt:<2} | Čas: {recipe.prep_time}")
            else:
                print(f"[{idx}/{total_targets}] ✗ Recepta na {link} ni bilo mogoče razčleniti.")

        return recipes


def save_to_json(recipes: List[Recipe], output_path: str) -> None:
    """
    Izvozi zbrane recepte v lepo formatirano JSON datoteko.
    """
    directory = os.path.dirname(output_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    # Shranimo neposredno kot seznam slovarjev receptov (brez ovitka z metapodatki)
    data = [r.to_dict() for r in recipes]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n[USPEH] Zbrani podatki uspešno shranjeni v JSON: {output_path}")


def save_to_xml(recipes: List[Recipe], output_path: str) -> None:
    """
    Opcijski izvoz v XML format za izpolnitev dodatnih zahtev po večpredstavnostnem shranjevanju.
    """
    directory = os.path.dirname(output_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    root = ET.Element("recepti", total=str(len(recipes)))

    for r in recipes:
        el_rec = ET.SubElement(root, "recept", id=r.recipe_id)
        ET.SubElement(el_rec, "naslov").text = r.title
        ET.SubElement(el_rec, "url").text = r.url
        ET.SubElement(el_rec, "kategorija").text = r.category
        ET.SubElement(el_rec, "casPriprave").text = r.prep_time
        ET.SubElement(el_rec, "zahtevnost").text = r.difficulty
        ET.SubElement(el_rec, "ocena").text = str(r.rating) if r.rating is not None else ""
        ET.SubElement(el_rec, "slika").text = r.image_url
        ET.SubElement(el_rec, "avtor").text = r.author

        # Sestavine
        el_sest = ET.SubElement(el_rec, "sestavine")
        for ing in r.ingredients:
            el_ing = ET.SubElement(el_sest, "sestavina")
            ET.SubElement(el_ing, "naziv").text = ing.name
            ET.SubElement(el_ing, "kolicina").text = str(ing.amount) if ing.amount is not None else ""
            ET.SubElement(el_ing, "enota").text = ing.unit or ""
            ET.SubElement(el_ing, "celotenTekst").text = ing.raw_text

        # Postopek
        el_postopek = ET.SubElement(el_rec, "postopek")
        for korak in r.instructions:
            ET.SubElement(el_postopek, "korak").text = korak

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    print(f"[USPEH] Zbrani podatki uspešno shranjeni v XML: {output_path}")


def main():
    """Vstopna točka za zagon prek ukazne vrstice."""
    parser = argparse.ArgumentParser(
        description="Razčlenjevalnik in strgalnik slovenskih kulinaričnih receptov (Kulinarika.net) za projekt Anti-Wolt."
    )
    parser.add_argument(
        "--category",
        type=str,
        default="vse",
        choices=list(RecipeScraper.AVAILABLE_CATEGORIES.keys()),
        help="Kategorija jedi za zbiranje (privzeto: 'vse')"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Največje število receptov za zajem (privzeto: 30, za več vnesite npr. 100)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.4,
        help="Premor v sekundah med zahtevki (privzeto: 0.4 s)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "data", "recipes.json"),
        help="Izhodna pot za shranjevanje JSON datoteke"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="Izvozi podatke v JSON format (privzeto vključeno)"
    )
    parser.add_argument(
        "--xml",
        action="store_true",
        help="Če je navedeno, izvozi podatke tudi v XML format"
    )

    args = parser.parse_args()

    scraper = RecipeScraper(delay_sec=args.delay)
    recipes = scraper.scrape(category=args.category, limit=args.limit)

    if recipes:
        save_to_json(recipes, args.output)

        if args.xml:
            xml_output = args.output.replace(".json", ".xml")
            save_to_xml(recipes, xml_output)

        # Povzetek
        total_ing = sum(len(r.ingredients) for r in recipes)
        avg_ing = total_ing / len(recipes) if recipes else 0
        print("\n" + "=" * 65)
        print("POVZETEK ZAJETIH PODATKOV:")
        print(f"  * Skupaj uspešno razčlenjenih receptov: {len(recipes)}")
        print(f"  * Skupaj prepoznanih sestavin:          {total_ing}")
        print(f"  * Povprečno število sestavin na recept: {avg_ing:.1f}")
        print("=" * 65 + "\n")
    else:
        print("[OPOZORILO] Noben recept ni bil uspešno zajet.")


if __name__ == "__main__":
    main()
