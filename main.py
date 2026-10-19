import os
import sys
import json
import xml.etree.ElementTree as ET
from datetime import datetime

# Zagotovitev pravilnega UTF-8 izpisa na Windows konzolah
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


class Kategorija:
    def __init__(self, id_kat, naziv, temperatura_c, datum_spremembe, opomba):
        self.id = id_kat
        self.naziv = naziv
        self.temperatura_c = float(temperatura_c) if temperatura_c else None
        self.datum_spremembe = datum_spremembe
        self.opomba = opomba


class Zivilo:
    def __init__(self, id_ziv, naziv, kategorija_id, kolicina, enota,
                 rok_uporabe, cena_eur, datum_vnosa, opomba):
        self.id = id_ziv
        self.naziv = naziv
        self.kategorija_id = kategorija_id
        self.kolicina = float(kolicina) if kolicina else 0.0
        self.enota = enota
        self.rok_uporabe = rok_uporabe
        self.cena_eur = float(cena_eur) if cena_eur else 0.0
        self.datum_vnosa = datum_vnosa
        self.opomba = opomba

        # Polje, ki ga dopolnimo ob povezovanju s kategorijami
        self.kategorija_naziv = "Neznano"

    def to_dict(self):
        return {
            "id": self.id,
            "naziv": self.naziv,
            "kolicina": self.kolicina,
            "enota": self.enota,
            "rokUporabe": self.rok_uporabe,
            "ocenjenaCenaEur": self.cena_eur,
            "datumVnosa": self.datum_vnosa,
            "kategorija": {
                "id": self.kategorija_id,
                "naziv": self.kategorija_naziv
            },
            "opomba": self.opomba if self.opomba else None
        }


def nalozi_kategorije(pot):
    kategorije = {}
    drevo = ET.parse(pot)
    koren = drevo.getroot()

    for el in koren.findall("kategorija"):
        id_kat = el.get("id")
        naziv = el.findtext("naziv", default="")
        temperatura = el.findtext("temperaturaC", default="")
        datum = el.findtext("datumSpremembe", default="")
        opomba = el.findtext("opomba", default="")

        kategorije[id_kat] = Kategorija(id_kat, naziv, temperatura, datum, opomba)
    return kategorije


def nalozi_zivila(pot):
    zivila = []
    drevo = ET.parse(pot)
    koren = drevo.getroot()

    for el in koren.findall("zivilo"):
        id_ziv = el.get("id")
        naziv = el.findtext("naziv", default="")
        kat_id = el.findtext("kategorijaId", default="")
        kolicina = el.findtext("kolicina", default="0")
        enota = el.findtext("enota", default="")
        rok = el.findtext("rokUporabe", default="")
        cena = el.findtext("ocenjenaCenaEur", default="0")
        datum_vnosa = el.findtext("datumVnosa", default="")
        opomba = el.findtext("opomba", default="")

        zivila.append(Zivilo(
            id_ziv, naziv, kat_id, kolicina, enota,
            rok, cena, datum_vnosa, opomba
        ))
    return zivila


def povezi_podatke(zivila, kategorije):
    for z in zivila:
        if z.kategorija_id in kategorije:
            z.kategorija_naziv = kategorije[z.kategorija_id].naziv


def izpisi_tabelo(zivila):
    if not zivila:
        print("\nNi najdenih živil za izbrani filter.")
        return

    print("\n" + "=" * 90)
    glava = f"{'ID':<8} | {'Naziv živila':<28} | {'Količina':<12} | {'Rok uporabe':<12} | {'Kategorija':<20}"
    print(glava)
    print("-" * 90)

    for z in zivila:
        kol = f"{z.kolicina} {z.enota}"
        vrstica = f"{z.id:<8} | {z.naziv:<28} | {kol:<12} | {z.rok_uporabe:<12} | {z.kategorija_naziv:<20}"
        print(vrstica)
    print("=" * 90)
    print(f"Skupaj najdenih zapisov: {len(zivila)}\n")


def izvozi_json(zivila, pot_datoteke="filtrirano.json"):
    podatki = [z.to_dict() for z in zivila]
    with open(pot_datoteke, "w", encoding="utf-8") as f:
        json.dump(podatki, f, ensure_ascii=False, indent=4)
    print(f"-> Rezultati uspešno shranjeni v JSON: {pot_datoteke}")


def izvozi_xml(zivila, pot_datoteke="filtrirano.xml"):
    koren = ET.Element("filtriranaZivila")

    for z in zivila:
        el_zivilo = ET.SubElement(koren, "zivilo", id=z.id)
        ET.SubElement(el_zivilo, "naziv").text = z.naziv
        ET.SubElement(el_zivilo, "kolicina").text = str(z.kolicina)
        ET.SubElement(el_zivilo, "enota").text = z.enota
        ET.SubElement(el_zivilo, "rokUporabe").text = z.rok_uporabe
        ET.SubElement(el_zivilo, "ocenjenaCenaEur").text = f"{z.cena_eur:.2f}"
        ET.SubElement(el_zivilo, "datumVnosa").text = z.datum_vnosa

        el_kat = ET.SubElement(el_zivilo, "kategorija", id=z.kategorija_id)
        el_kat.text = z.kategorija_naziv

        el_opomba = ET.SubElement(el_zivilo, "opomba")
        el_opomba.text = z.opomba if z.opomba else ""

    drevo = ET.ElementTree(koren)
    ET.indent(drevo, space="    ")
    drevo.write(pot_datoteke, encoding="utf-8", xml_declaration=True)
    print(f"-> Rezultati uspešno shranjeni v XML: {pot_datoteke}")


def varen_vnos(poziv, privzeto=""):
    try:
        vrednost = input(poziv).strip()
        return vrednost if vrednost else privzeto
    except (EOFError, KeyboardInterrupt):
        return privzeto


def filtriraj(zivila):
    print("Izberite filter za pregled hladilnika:")
    print("1 - Živila, ki jim kmalu poteče rok uporabe (rok <= izbrani datum)")
    print("2 - Živila z nizko zalogo (količina <= meja)")
    print("3 - Živila po kategoriji")
    print("4 - Prikaz vseh živil")

    izbira = varen_vnos("Vnesite izbiro (1-4) [privzeto 1]: ", "1")

    if izbira == "1":
        privzeti_datum = "2026-10-24"
        mejni_datum = varen_vnos(f"Vnesite mejni datum (YYYY-MM-DD) [privzeto {privzeti_datum}]: ", privzeti_datum)
        return [z for z in zivila if z.rok_uporabe and z.rok_uporabe <= mejni_datum]

    elif izbira == "2":
        vnos = varen_vnos("Vnesite maksimalno količino za opozorilo [privzeto 1.0]: ", "1.0")
        try:
            meja = float(vnos)
        except ValueError:
            meja = 1.0
        return [z for z in zivila if z.kolicina <= meja]

    elif izbira == "3":
        kategorije = sorted(list({z.kategorija_naziv for z in zivila if z.kategorija_naziv}))
        print("\nRazpoložljive kategorije:")
        for idx, kat in enumerate(kategorije, 1):
            print(f"  {idx}. {kat}")
        vnos_kat = varen_vnos("Vnesite ime ali številko kategorije [privzeto 1]: ", "1")
        if vnos_kat.isdigit() and 1 <= int(vnos_kat) <= len(kategorije):
            izbrana = kategorije[int(vnos_kat) - 1]
        else:
            izbrana = vnos_kat
        return [z for z in zivila if z.kategorija_naziv.lower() == izbrana.lower()]

    else:
        return zivila


def main():
    baza_mapa = os.path.join(os.path.dirname(__file__), "podatki")
    pot_kategorije = os.path.join(baza_mapa, "kategorije.xml")
    pot_zivila = os.path.join(baza_mapa, "zivila.xml")

    print("--- Anti-Wolt: Čistilec Hladilnika (Upravljanje živil) ---")
    print("Nalaganje podatkov iz XML...")

    kategorije = nalozi_kategorije(pot_kategorije)
    zivila = nalozi_zivila(pot_zivila)

    povezi_podatke(zivila, kategorije)
    print(f"Uspešno naloženo: {len(kategorije)} kategorij in {len(zivila)} živil.\n")

    filtrirana = filtriraj(zivila)
    izpisi_tabelo(filtrirana)

    izvozi_json(filtrirana, "filtrirano.json")
    izvozi_xml(filtrirana, "filtrirano.xml")
    print("\nObdelava končana.")


if __name__ == "__main__":
    main()
