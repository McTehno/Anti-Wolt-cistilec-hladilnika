"""
Modul za razčlenjevanje (parsing) HTML vsebine spletnih strani Kulinarika.net.

Ta modul vsebuje funkcije za:
1. Iskanje in ekstrakcijo povezav do receptov na seznamih kategorij.
2. Razčlenjevanje posameznih strani recepta v strukturirane objekte Recipe in Ingredient.
3. Normalizacijo merskimi enot in numeričnih količin za kasnejšo primerjavo z zalogo v hladilniku.
"""

import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin
from bs4 import BeautifulSoup

try:
    from .models import Recipe, Ingredient
except (ImportError, ValueError):
    from models import Recipe, Ingredient


# Pogoste slovenske kulinarične merske enote, urejene po dolžini (daljše najprej,
# da preprečimo delno ujemanje npr. 'žlička' pred 'žlica' ali 'dag' pred 'g')
KNOWN_UNITS = [
    "žlička", "žličke", "žličk", "žličko",
    "žlica", "žlice", "žlici", "žlico",
    "ščepec", "ščepca", "ščep",
    "skodelica", "skodelice", "skodelico",
    "lonček", "lončka", "lončki",
    "strok", "stroka", "stroki",
    "glavica", "glavice", "glavico",
    "zavitek", "zavitka", "paket",
    "rezina", "rezine", "rezin",
    "kos", "kosa", "kosi",
    "dcl", "dl", "ml", "cl", "l",
    "dag", "kg", "g"
]


def parse_quantity_and_unit(raw_quantity: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Razčleni surovo besedilo količine (npr. '500 g', '0,5 l', '2 1/2 žlice', '3')
    v numerično vrednost (float) ter standardizirano mersko enoto.

    Argumenti:
        raw_quantity: Surovi niz znakov iz oznake s količino.

    Vrača:
        Tuple (kolicina_float, enota_str)
    """
    if not raw_quantity:
        return None, None

    # Normalizacija presledkov in zamenjava slovenske decimalne vejice s piko
    cleaned = raw_quantity.strip().lower().replace(",", ".")

    # Odstrani predpone kot so 'cca.', 'približno', 'pribl.'
    cleaned = re.sub(r'^(?:cca\.?|približno|pribl\.?)\s*', '', cleaned)

    # 1. Obravnava ulomkov (npr. '1/2', '1/4', '3/4')
    fraction_map = {
        "1/2": 0.5,
        "1/4": 0.25,
        "3/4": 0.75,
        "1/3": 0.33,
        "2/3": 0.67
    }
    for frac_str, frac_val in fraction_map.items():
        if frac_str in cleaned:
            cleaned = cleaned.replace(frac_str, str(frac_val))
            break

    # 2. Iskanje numeričnega dela na začetku niza (podpira cela števila in decimalke)
    num_match = re.search(r'(\d+(?:\.\d+)?)', cleaned)
    amount: Optional[float] = None
    if num_match:
        try:
            amount = float(num_match.group(1))
        except ValueError:
            amount = None

    # 3. Iskanje merske enote v preostanku besedila
    unit: Optional[str] = None
    for known_unit in KNOWN_UNITS:
        # Preverimo, ali se enota pojavi kot samostojna beseda ali na koncu
        pattern = r'\b' + re.escape(known_unit) + r'\b'
        if re.search(pattern, cleaned):
            # Normalizacija enot za enoten zapis v bazi
            if known_unit in ["žlička", "žličke", "žličk", "žličko"]:
                unit = "žlička"
            elif known_unit in ["žlica", "žlice", "žlici", "žlico"]:
                unit = "žlica"
            elif known_unit in ["dcl", "dl"]:
                unit = "dl"
            elif known_unit in ["kos", "kosa", "kosi"]:
                unit = "kos"
            elif known_unit in ["strok", "stroka", "stroki"]:
                unit = "strok"
            elif known_unit in ["ščepec", "ščepca", "ščep"]:
                unit = "ščepec"
            else:
                unit = known_unit
            break

    return amount, unit


def parse_ingredient_element(label_text: str, name_text: str) -> Optional[Ingredient]:
    """
    Sestavi objekt Ingredient iz ločenih delov količine in naziva.
    """
    clean_name = name_text.strip()
    if not clean_name:
        return None

    # Očisti odvečne ločila na začetku ali koncu naziva
    clean_name = re.sub(r'^[,\-\s]+|[,\-\s]+$', '', clean_name)

    amount, unit = parse_quantity_and_unit(label_text)

    # Celotno besedilo za intuitiven prikaz
    if label_text.strip():
        raw_text = f"{label_text.strip()} {clean_name}"
    else:
        raw_text = clean_name

    return Ingredient(
        name=clean_name,
        amount=amount,
        unit=unit,
        raw_text=raw_text
    )


def extract_recipe_links(html_content: str, base_url: str = "https://www.kulinarika.net") -> List[str]:
    """
    Iz seznama receptov (stran z recepti ali kategorija) poišče vse veljavne URL povezave
    do posameznih receptov.

    Povezave imajo obliko: /recepti/<kategorija>/.../<slug>/<id>/
    """
    soup = BeautifulSoup(html_content, "lxml")
    unique_links: List[str] = []
    seen = set()

    # Vzorec: pot se začne z /recepti/, sledi vsaj ena raven kategorije in se konča z ID-jem ter poševnico
    recipe_pattern = re.compile(r'^/recepti/.+/(\d+)/?$')

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        # Izločimo navigacijske strani (npr. lestvica, vprašaj luno, seznam)
        if any(skip in href for skip in ["/seznam/", "/lestvica/", "/vprasaj-luno/", "/iskanje/", "/oddaj/"]):
            continue

        match = recipe_pattern.match(href)
        if match:
            full_url = urljoin(base_url, href)
            if full_url not in seen:
                seen.add(full_url)
                unique_links.append(full_url)

    return unique_links


def parse_recipe_page(html_content: str, url: str) -> Optional[Recipe]:
    """
    Prebere celotno HTML stran posameznega recepta in izlušči vse podatke v objekt Recipe.

    Argumenti:
        html_content: HTML koda strani recepta.
        url: Celoten URL strani (uporabi se za izpeljavo ID-ja in kategorije).

    Vrača:
        Objekt Recipe ali None, če strani ni mogoče prebrati kot veljaven recept.
    """
    soup = BeautifulSoup(html_content, "lxml")

    # 1. Naslov recepta (vedno v <h1>)
    h1_tag = soup.find("h1")
    if not h1_tag:
        # Če ni <h1>, to ni veljavna stran z receptom
        return None
    title = h1_tag.get_text(strip=True)

    # 2. Identifikator recepta (številke na koncu URL-ja)
    id_match = re.search(r'/(\d+)/?$', url)
    recipe_id = id_match.group(1) if id_match else "neznano"

    # 3. Kategorija in podkategorije iz URL naslova
    # Primer URL-ja: .../recepti/mesne-jedi/sesekljano-meso/klasicna-lazanja/14392/
    url_parts = [p for p in url.split("/") if p]
    category = "splošno"
    subcategories: List[str] = []
    if "recepti" in url_parts:
        idx = url_parts.index("recepti")
        remaining = url_parts[idx + 1:]
        # Zadnji element je ID, predzadnji je slug recepta
        if len(remaining) >= 3:
            category = remaining[0]
            subcategories = remaining[1:-2]
        elif len(remaining) >= 2:
            category = remaining[0]

    # 4. Kratek opis recepta
    description = ""
    desc_tag = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    if desc_tag and desc_tag.get("content"):
        description = desc_tag["content"].strip()
    else:
        lead_p = soup.find("p", class_="lead")
        if lead_p:
            description = lead_p.get_text(strip=True)

    # 5. Čas priprave
    prep_time = ""
    time_tag = soup.find(class_=lambda c: c and "cas-vrednost" in c) or soup.find(class_=lambda c: c and "cas-priprave" in c)
    if time_tag:
        prep_time = time_tag.get_text(strip=True)

    # 6. Zahtevnost kuhanja
    difficulty = ""
    diff_tag = soup.find(class_=lambda c: c and "zahtevnost" in c and "collapsible" in c)
    if diff_tag:
        # Počisti morebiten napis 'Zahtevnost' v znački
        difficulty = diff_tag.get_text(strip=True).replace("Zahtevnost", "").strip()

    # 7. Ocena in število ocen
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    rating_val_tag = soup.find(class_=lambda c: c and "ocena-vrednost" in c)
    if rating_val_tag:
        try:
            rating = float(rating_val_tag.get_text(strip=True).replace(",", "."))
        except ValueError:
            rating = None

    rating_cnt_tag = soup.find(class_=lambda c: c and "ocena-stevilo" in c)
    if rating_cnt_tag:
        cnt_match = re.search(r'(\d+)', rating_cnt_tag.get_text())
        if cnt_match:
            try:
                rating_count = int(cnt_match.group(1))
            except ValueError:
                rating_count = None

    # 8. Slika jedi
    image_url = ""
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        image_url = og_img["content"].strip()

    # 9. Avtor recepta
    author = ""
    author_tag = soup.find(class_=lambda c: c and "podatki-avtor" in c)
    if author_tag:
        author = author_tag.get_text(strip=True)

    # 10. Sestavine (uporabimo strukturirane Schema.org značke v div#sestavine)
    ingredients: List[Ingredient] = []
    sestavine_container = soup.find(id="sestavine")
    if sestavine_container:
        # Ponavadi so sestavine v <p class="cf" itemprop="recipeIngredient">
        p_items = sestavine_container.find_all("p", itemprop="recipeIngredient")
        if not p_items:
            # Rezervni mehanizem, če ni itemprop značke
            p_items = sestavine_container.find_all("p")

        for p in p_items:
            label_tag = p.find(class_="label")
            val_tag = p.find(class_="label-value")

            lbl_str = label_tag.get_text(strip=True) if label_tag else ""
            val_str = val_tag.get_text(strip=True) if val_tag else p.get_text(strip=True)

            ing = parse_ingredient_element(lbl_str, val_str)
            if ing:
                ingredients.append(ing)

    # 11. Navodila za pripravo (koraki)
    instructions: List[str] = []
    postopek_container = soup.find(id="postopek")
    if postopek_container:
        # Poskusimo poiskati posamezne korake
        step_items = postopek_container.find_all(["p", "div"], itemprop=lambda x: x in ["text", "recipeInstructions"])
        for item in step_items:
            txt = item.get_text(separator=" ", strip=True)
            if txt and txt not in instructions:
                instructions.append(txt)

        # Če ni označenih korakov, vzamemo celotno besedilo vsebovalnika
        if not instructions:
            full_text = postopek_container.get_text(separator="\n", strip=True)
            if full_text:
                instructions = [line.strip() for line in full_text.split("\n") if line.strip()]

    return Recipe(
        recipe_id=recipe_id,
        title=title,
        url=url,
        category=category,
        subcategories=subcategories,
        description=description,
        prep_time=prep_time,
        difficulty=difficulty,
        rating=rating,
        rating_count=rating_count,
        image_url=image_url,
        author=author,
        ingredients=ingredients,
        instructions=instructions
    )
