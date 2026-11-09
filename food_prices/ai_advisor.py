import os
import re
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests

# Konfiguracija beleženja za diagnostiko API klicev
logger = logging.getLogger(__name__)

# Nalaganje enotne .env datoteke iz korena projekta
ENV_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ENV_FILE)
except ImportError:
    # Če knjižnica python-dotenv še ni nameščena, preberemo enotno .env datoteko
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class AiRecipeRecommendation:
    """
    Namenski Python razred za deserializacijo prejetih strukturiranih JSON podatkov iz LLM.
    
    Vsebuje vse ključne atribute, ki jih vrne model:
    - ime_jedi: Naziv predlaganega recepta
    - kratek_opis: Kratek povzetek jedi in zakaj je primerna za porabo ostankov
    - cas_priprave_min: Ocenjen čas kuhanja v minutah
    - zahtevnost: Težavnostna stopnja (Enostavno, Srednje, Zahtevno)
    - porcije: Število porcij
    - uporabljene_sestavine: Seznam sestavin, ki jih je uporabnik imel v hladilniku
    - dodatne_sestavine: Osnovne začimbe ali dodatki iz shrambe (sol, olje, poper...)
    - postopek: Koraki priprave po vrstnem redu
    - ocena_stroska_doma_eur: Približen finančni strošek sestavin doma
    - ocena_prihranka_napram_dostavi_eur: Koliko uporabnik prihrani napram naročilu (Wolt/Glovo)
    - nasvet_zero_waste: Praktičen nasvet za zmanjšanje zavržene hrane
    - model_used: Ime uporabljenega LLM modela
    - cas_generiranja: Časovna značka klica
    """
    ime_jedi: str
    kratek_opis: str
    cas_priprave_min: int
    zahtevnost: str
    porcije: int
    uporabljene_sestavine: List[str] = field(default_factory=list)
    dodatne_sestavine: List[str] = field(default_factory=list)
    postopek: List[str] = field(default_factory=list)
    ocena_stroska_doma_eur: float = 0.0
    ocena_prihranka_napram_dostavi_eur: float = 0.0
    nasvet_zero_waste: str = ""
    model_used: str = ""
    cas_generiranja: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any], model_name: str = "") -> "AiRecipeRecommendation":
        """
        Metoda za deserializacijo surovega slovarja (razčlenjenega JSON-a) v primerek razreda.
        Izvaja varno pretvorbo tipov (npr. numerične pretvorbe za minute in evre).
        """
        # Varno pretvorimo čas priprave v celo število
        try:
            cas_min = int(data.get("cas_priprave_min", 20))
        except (ValueError, TypeError):
            cas_min = 20

        # Varno pretvorimo porcije
        try:
            porcije = int(data.get("porcije", 2))
        except (ValueError, TypeError):
            porcije = 2

        # Varno pretvorimo finančne zneske v float
        try:
            strosek = float(data.get("ocena_stroska_doma_eur", 2.50))
        except (ValueError, TypeError):
            strosek = 2.50

        try:
            prihranek = float(data.get("ocena_prihranka_napram_dostavi_eur", 8.00))
        except (ValueError, TypeError):
            prihranek = 8.00

        # Poskrbimo, da so seznami res seznami nizov
        def sanitize_list(val: Any) -> List[str]:
            if isinstance(val, list):
                return [str(item).strip() for item in val if item]
            elif isinstance(val, str):
                return [s.strip() for s in val.split("\n") if s.strip()]
            return []

        return cls(
            ime_jedi=str(data.get("ime_jedi", "Okusna domača jed iz ostankov")),
            kratek_opis=str(data.get("kratek_opis", "Jed pripravljena iz razpoložljivih sestavin.")),
            cas_priprave_min=cas_min,
            zahtevnost=str(data.get("zahtevnost", "Enostavno")),
            porcije=porcije,
            uporabljene_sestavine=sanitize_list(data.get("uporabljene_sestavine", [])),
            dodatne_sestavine=sanitize_list(data.get("dodatne_sestavine", [])),
            postopek=sanitize_list(data.get("postopek", [])),
            ocena_stroska_doma_eur=round(strosek, 2),
            ocena_prihranka_napram_dostavi_eur=round(prihranek, 2),
            nasvet_zero_waste=str(data.get("nasvet_zero_waste", "Hrano hranite v nepredušnih posodah.")),
            model_used=model_name
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Pretvori primerek razreda nazaj v slovar (za lažjo serializacijo v Flask JSON odgovor).
        """
        return asdict(self)


class OpenRouterAiService:
    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
    API_URL = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, api_url: Optional[str] = None):
        """
        Inicializacija odjemalca za OpenRouter z vrednostmi iz .env.
        """
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "").strip()
        self.model = model or os.getenv("OPENROUTER_MODEL", self.DEFAULT_MODEL).strip()
        self.api_url = api_url or os.getenv("OPENROUTER_API_URL", self.API_URL).strip()

    def is_configured(self) -> bool:
        """Preveri, ali imamo na voljo veljaven API ključ."""
        return bool(self.api_key and len(self.api_key) > 10)

    def generate_recipe(
        self,
        ingredients: str,
        max_time_min: Optional[int] = None,
        servings: int = 2,
        meal_type: str = "Vseeno",
        diet_preference: str = "Brez omejitev",
        client_context: Optional[str] = None
    ) -> AiRecipeRecommendation:
        """
        :raises ValueError: Če manjka API ključ ali če je odgovor modela neveljaven.
        :raises requests.RequestException: Ob napakah omrežja ali HTTP klicih.
        """
        if not self.is_configured():
            raise ValueError(
                "OpenRouter API ključ ni nastavljen! Prosimo preverite datoteko .env "
                "in nastavite spremenljivko OPENROUTER_API_KEY."
            )

        # Čiščenje vhodnih podatkov
        clean_ingredients = ingredients.strip() if ingredients else "osnovne sestavine v hladilniku"
        time_text = f"največ {max_time_min} minut" if max_time_min else "kratkem času (brez strogih omejitev)"
        context_text = client_context.strip() if client_context else "trenutni čas kuhanja"

        system_prompt = (
            "Ti si vrhunski kuharski chef in asistent projekta 'Anti-Wolt: Čistilec Hladilnika'. "
            "Tvoja glavna naloga je pomagati uporabniku, da iz živil, ki jih ima doma, "
            "pripravi okusen domač obrok, se izogne metanju hrane v smeti ter prihrani denar "
            "v primerjavi z naročanjem dostave hrane (Wolt / Glovo).\n\n"
            "POMEMBNO NAVODILO:\n"
            "Tvoj odgovor MORA biti izključno veljaven JSON objekt v slovenščini, "
            "brez kakršnegakoli uvodnega ali zaključnega besedila zunaj JSON strukture.\n"
            "Uporabi točno naslednjo JSON shemo:\n"
            "{\n"
            '  "ime_jedi": "Naziv jedi",\n'
            '  "kratek_opis": "Kratek privlačen opis jedi in zakaj je primerna za ostanke",\n'
            '  "cas_priprave_min": 20,\n'
            '  "zahtevnost": "Enostavno",\n'
            '  "porcije": 2,\n'
            '  "uporabljene_sestavine": ["sestavina 1", "sestavina 2"],\n'
            '  "dodatne_sestavine": ["sol", "olje", "začimba"],\n'
            '  "postopek": ["1. korak...", "2. korak...", "3. korak..."],\n'
            '  "ocena_stroska_doma_eur": 2.40,\n'
            '  "ocena_prihranka_napram_dostavi_eur": 9.10,\n'
            '  "nasvet_zero_waste": "Praktičen nasvet kako porabiti ostanke hrane ali jih shraniti"\n'
            "}"
        )

        user_prompt = (
            f"Dinamični podatki uporabnika:\n"
            f"- Sestavine v hladilniku/shrambi: {clean_ingredients}\n"
            f"- Razpoložljiv čas priprave: {time_text}\n"
            f"- Število oseb / porcij: {servings}\n"
            f"- Želeni tip obroka: {meal_type}\n"
            f"- Prehranska preferenca: {diet_preference}\n"
            f"- Kontekstualni čas in okolje: {context_text}\n\n"
            f"Navodilo: Sestavi odličen domač recept, ki maksimalno izkoristi zgoraj navedene sestavine. "
            f"Oceni realen domači strošek sestavin ter prihranek v primerjavi s povprečnim naročilom dostave "
            f"(kjer en obrok s poštnino stane okoli 11-13 € na osebo). "
            f"Vrni izključno zahtevani JSON objekt."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5000",
            "X-Title": "Anti-Wolt Food Assistant"
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.4
        }

        logger.info(f"Pošiljam zahtevek na OpenRouter API (model: {self.model})...")

        # KLIC ODPRTEGA MODELA PREK OPENROUTER API
        response = requests.post(
            self.api_url,
            headers=headers,
            json=payload,
            timeout=45
        )

        if response.status_code != 200:
            logger.error(f"OpenRouter API napaka {response.status_code}: {response.text}")
            try:
                err_json = response.json()
                msg = err_json.get("error", {}).get("message", response.text)
            except Exception:
                msg = response.text
            raise ValueError(f"Napaka ponudnika OpenRouter ({response.status_code}): {msg}")

        res_data = response.json()
        raw_content = res_data["choices"][0]["message"]["content"]
        logger.info("Odgovor iz LLM uspešno prejet. Začenjam razčlenjevanje JSON...")

        # ČIŠČENJE JSON ODGOVORA
        # Včasih modeli dodajo markdown ovojnico ```json ... ``` kljub json_object navodilu
        json_clean = raw_content.strip()
        if "```" in json_clean:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", json_clean)
            if match:
                json_clean = match.group(1).strip()

        try:
            parsed_dict = json.loads(json_clean)
        except json.JSONDecodeError as err:
            logger.error(f"Neveljaven JSON iz LLM: {raw_content}")
            raise ValueError(f"Model ni vrnil veljavnega JSON formata: {err}")

        # DESERIALIZACIJA V NAMENSKI PYTHON RAZRED
        recommendation = AiRecipeRecommendation.from_dict(parsed_dict, model_name=self.model)
        logger.info(f"Uspešno deserializirano v AiRecipeRecommendation: {recommendation.ime_jedi}")
        return recommendation
