"""
Modul za pridobivanje PCAXIS (.px) podatkov iz portala OPSI / SiStat (SURS).

Ta modul je zadolžen za prenos uradnih statističnih podatkov o povprečnih
drobnoprodajnih cenah živil in storitev (tabela 0411005S.px) iz podatkovne baze SiStat
(PxWeb v1 API), ki je indeksirana v nacionalnem portalu odprtih podatkov (OPSI).
Vključuje mehanizem lokalnega predpomnjenja (caching) za hitrejši zagon ter možnost
dinamičnega osveževanja podatkov neposredno iz oddaljenega vira ob zahtevi.
"""

import os
import json
import logging
import urllib.request
import urllib.error
from typing import Optional

# Konfiguracija beleženja dogodkov (logging)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Privzeta URL povezava do PxWeb API končne točke za tabelo 0411005S.px
# Tabela vsebuje povprečne drobnoprodajne cene izdelkov in storitev v Sloveniji (letno)
SISTAT_API_URL = "https://pxweb.stat.si/SiStatData/api/v1/sl/Data/0411005S.px"

# Privzeta lokalna pot za shranjevanje .px datoteke v projektni mapi
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DEFAULT_PX_FILENAME = "0411005S.px"
DEFAULT_PX_PATH = os.path.join(DEFAULT_DATA_DIR, DEFAULT_PX_FILENAME)


class SiStatClient:
    """
    Odjemalec za komunikacijo z vmesnikom PxWeb baze SiStat (SURS / OPSI).
    
    Omogoča prenos celotne PCAXIS datoteke ali preverjanje metapodatkov tabele.
    """

    def __init__(self, api_url: str = SISTAT_API_URL, cache_path: str = DEFAULT_PX_PATH):
        """
        Inicializacija odjemalca.

        :param api_url: URL naslov PxWeb API končne točke za izbrano tabelo.
        :param cache_path: Lokalna datotečna pot, kamor se shrani prenesena .px datoteka.
        """
        self.api_url = api_url
        self.cache_path = cache_path

        # Zagotovimo obstoj ciljne mape za shranjevanje podatkov
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)

    def fetch_dataset(self, force_refresh: bool = False) -> str:
        """
        Pridobi PCAXIS (.px) datoteko. Če datoteka že obstaja lokalno in force_refresh=False,
        uporabi lokalno predpomnjeno kopijo. Če datoteke ni ali je zahtevana osvežitev,
        sproži klic na oddaljeni strežnik SiStat.

        :param force_refresh: Zastavica, ki prisili ponovni prenos s strežnika,
                              tudi če lokalna datoteka že obstaja.
        :return: Absolutna pot do veljavne lokalne .px datoteke.
        """
        if not force_refresh and os.path.exists(self.cache_path):
            file_size = os.path.getsize(self.cache_path)
            # Preverimo, da datoteka ni prazna ali poškodovana
            if file_size > 1024:
                logger.info(f"Uporabljam lokalno predpomnjeno datoteko: {self.cache_path} ({file_size} bajtov)")
                return self.cache_path
            else:
                logger.warning("Lokalna datoteka je premajhna ali neveljavna, sprožam ponovni prenos.")

        logger.info(f"Prenašam sveže podatke iz SiStat API: {self.api_url} ...")

        # Priprava zahteve za PxWeb API:
        # Prazna poizvedba (query: []) pomeni zajem vseh podatkov tabele.
        # Format "px" sporoči strežniku, naj vrne vsebino v PC-Axis formatu.
        payload = json.dumps({
            "query": [],
            "response": {
                "format": "px"
            }
        }).encode('utf-8')

        headers = {
            'User-Agent': 'Anti-Wolt-TIDS-Project/1.0 (Student Project; FERI UM)',
            'Content-Type': 'application/json',
            'Accept': 'text/plain, application/octet-stream, */*'
        }

        request = urllib.request.Request(
            url=self.api_url,
            data=payload,
            headers=headers,
            method='POST'
        )

        try:
            # Časovna omejitev (timeout) 30 sekund za stabilnost pri počasnejših omrežjih
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()

            if not content or len(content) < 500:
                raise ValueError("Prejeta vsebina iz API-ja je prazna ali prekratka za veljaven PCAXIS format.")

            # Shranjevanje surovih bajtov v lokalno datoteko (kodiranje je tipično windows-1250)
            with open(self.cache_path, "wb") as f_out:
                f_out.write(content)

            logger.info(f"Uspešno preneseno in shranjeno v {self.cache_path} ({len(content)} bajtov).")
            return self.cache_path

        except urllib.error.HTTPError as http_err:
            logger.error(f"HTTP napaka pri klicu SiStat API: koda {http_err.code} - {http_err.reason}")
            # Če oddaljeni klic odpove, a imamo staro kopijo, jo uporabimo kot rešilno bilko (fallback)
            if os.path.exists(self.cache_path) and os.path.getsize(self.cache_path) > 1024:
                logger.warning("Uporabljam obstoječo lokalno kopijo kot nadomestilo (fallback).")
                return self.cache_path
            raise RuntimeError(f"Neuspešen prenos podatkov iz SiStat (HTTP {http_err.code})") from http_err

        except Exception as err:
            logger.error(f"Nepričakovana napaka pri prenosu podatkov: {err}")
            if os.path.exists(self.cache_path) and os.path.getsize(self.cache_path) > 1024:
                logger.warning("Uporabljam obstoječo lokalno kopijo kot nadomestilo (fallback).")
                return self.cache_path
            raise


# Enostaven testni zagon neposredno iz konzole
if __name__ == "__main__":
    client = SiStatClient()
    path = client.fetch_dataset(force_refresh=True)
    print(f"Datoteka je pripravljena: {path}")

