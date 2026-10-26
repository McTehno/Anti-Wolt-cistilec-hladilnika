"""
Podatkovni modeli za recept in sestavine.

Ta modul definira strukturirane razrede za hranjenje podatkov o receptih,
ki jih zajamemo s spletne strani Kulinarika.net. Vsak razred vsebuje
metodo `to_dict()`, ki omogoča enostavno serializacijo v JSON format.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime


class Ingredient:
    """
    Predstavlja posamezno sestavino recepta.

    Vsebuje tako surove podatke (kako so zapisani na spletu),
    kot tudi razčlenjene numerične vrednosti in merske enote, kar je
    ključno za primerjavo s stanjem v hladilniku uporabnika.
    """

    def __init__(
        self,
        name: str,
        amount: Optional[float] = None,
        unit: Optional[str] = None,
        raw_text: str = ""
    ):
        # Ime sestavine (npr. 'mleto meso', 'čebula', 'sol')
        self.name: str = name.strip()

        # Numerična količina (npr. 500, 2.5, 1) - None če količina ni specificirana
        self.amount: Optional[float] = amount

        # Merska enota (npr. 'g', 'dag', 'kg', 'ml', 'dl', 'l', 'žlica', 'kos')
        self.unit: Optional[str] = unit.strip() if unit else None

        # Izvorni celotni niz za prikaz v uporabniškem vmesniku (npr. '500 g mletega mesa')
        self.raw_text: str = raw_text.strip() if raw_text else f"{amount or ''} {unit or ''} {name}".strip()

    def to_dict(self) -> Dict[str, Any]:
        """Pretvori sestavino v slovar, primeren za JSON izvoz."""
        return {
            "name": self.name,
            "amount": self.amount,
            "unit": self.unit,
            "raw_text": self.raw_text
        }


class Recipe:
    """
    Predstavlja celoten kulinarični recept.

    Vključuje vse ključne atribute jedi: metapodatke, hranilne ocene,
    seznam sestavin ter navodila za pripravo.
    """

    def __init__(
        self,
        recipe_id: str,
        title: str,
        url: str,
        category: str,
        subcategories: Optional[List[str]] = None,
        description: str = "",
        prep_time: str = "",
        difficulty: str = "",
        rating: Optional[float] = None,
        rating_count: Optional[int] = None,
        image_url: str = "",
        author: str = "",
        ingredients: Optional[List[Ingredient]] = None,
        instructions: Optional[List[str]] = None
    ):
        # Enolični identifikator recepta (izvlečen iz URL naslova, npr. '14600')
        self.recipe_id: str = str(recipe_id)

        # Naslov jedi
        self.title: str = title.strip()

        # Celoten URL naslov do originalnega vira
        self.url: str = url.strip()

        # Glavna kategorija (npr. 'testenine', 'enolončnice', 'mesne-jedi')
        self.category: str = category.strip()

        # Dodatne podkategorije za natančnejše filtriranje
        self.subcategories: List[str] = subcategories or []

        # Kratek povzetek ali opis jedi
        self.description: str = description.strip()

        # Čas kuhanja/priprave (npr. '45 min', '1 ura')
        self.prep_time: str = prep_time.strip()

        # Zahtevnost priprave (npr. '1/5', 'srednje')
        self.difficulty: str = difficulty.strip()

        # Povprečna ocena uporabnikov (od 1 do 5)
        self.rating: Optional[float] = rating

        # Število oddanih ocen uporabnikov
        self.rating_count: Optional[int] = rating_count

        # Povezava do reprezentativne fotografije jedi
        self.image_url: str = image_url.strip()

        # Uporabniško ime avtorja recepta
        self.author: str = author.strip()

        # Strukturiran seznam vseh sestavin
        self.ingredients: List[Ingredient] = ingredients or []

        # Seznam korakov priprave po vrstnem redu
        self.instructions: List[str] = instructions or []

    def to_dict(self) -> Dict[str, Any]:
        """
        Pretvori celoten recept v hierarhični slovar za shranjevanje v JSON datoteko.
        """
        return {
            "id": self.recipe_id,
            "title": self.title,
            "url": self.url,
            "category": self.category,
            "subcategories": self.subcategories,
            "description": self.description,
            "prep_time": self.prep_time,
            "difficulty": self.difficulty,
            "rating": self.rating,
            "rating_count": self.rating_count,
            "image_url": self.image_url,
            "author": self.author,
            "ingredient_count": len(self.ingredients),
            "ingredients": [ing.to_dict() for ing in self.ingredients],
            "instructions": self.instructions
        }
