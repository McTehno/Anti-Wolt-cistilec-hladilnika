# Modul: Statistične cene živil (OPSI & SiStat PCAXIS)

Ta modul predstavlja rešitev praktične naloge za integracijo **Odprtih podatkov Slovenije (OPSI)** in obdelavo statističnih datotek v formatu **PCAXIS (`.px`)** z uporabo knjižnice **`pyaxis`** ter dinamično vizualizacijo v **Chart.js**.

V okviru celovitega projekta **Anti-Wolt: Čistilec Hladilnika** ta modul zagotavlja uradne maloprodajne cene osnovnih živil in gostinskih storitev v Sloveniji, kar omogoča avtomatski izračun finančnega prihranka pri kuhanju doma iz ostankov v hladilniku napram naročanju dostave hrane prek Wolt/Glovo.

---

## 1. Uporabljeni podatki in vir

- **Vir podatkov:** [Nacionalni portal odprtih podatkov Slovenije (OPSI)](https://podatki.gov.si/) / [Baza SiStat Statističnega urada RS (SURS)](https://pxweb.stat.si/SiStat/sl)
- **Tabela / Matrika:** `0411005S.px`
- **Naziv zbrike:** *Povprečne drobnoprodajne cene izdelkov in storitev, Slovenija, letno*
- **Format podatkov:** **PCAXIS (`.px`)**
- **Časovna serija:** 2018–2025
- **Število zapisov:** 1.296 veljavnih cenovnih zapisov za več kot 160 posameznih izdelkov in storitev.

---

## 2. Zakaj PCAXIS in knjižnica `pyaxis`?

**PCAXIS (`.px`)** je mednarodni standardni večdimenzionalni format statističnih uradov (SURS v Sloveniji, SCB na Švedskem, BFS v Švici, INE v Španiji itd.). Format v eni sami datoteki združuje:
1. **Bogate metapodatke:** Naslov, avtorske pravice, merske enote, časovne žige posodobitev, šifrante spremenljivk ter večjezične oznake.
2. **Podatkovno matriko:** Večdimenzionalno tabelo vrednosti.

V programskem jeziku Python format razčlenjujemo s specializirano knjižnico **`pyaxis`**:
```python
from pyaxis import pyaxis

# Razčlenitev PCAXIS datoteke v DataFrame in slovar metapodatkov
px_data = pyaxis.parse('data/0411005S.px', encoding='cp1250')

# Metapodatki
metadata = px_data['METADATA']

# Glavni podatkovni okvir (Pandas DataFrame)
df = px_data['DATA']
```

---

## 3. Arhitektura in obdelava podatkov

Struktura modula je razdeljena na samostojne, visoko kohezivne komponente:

```
food_prices/
│
├── client.py            # Prenašalec podatkov iz SiStat PxWeb API (s samodejnim predpomnjenjem)
├── processor.py         # Razčlenjevalnik s pyaxis, kategorizacija, filtriranje in agregacije
├── app.py               # REST API strežnik (Flask) s podporo za dinamične poizvedbe
├── requirements.txt     # Odvisnosti modula
│
├── data/
│   └── 0411005S.px      # Lokalno predpomnjena PCAXIS datoteka
│
├── templates/
│   └── index.html       # Spletni vmesnik nadzorne plošče (Dashboard)
│
└── static/
    ├── css/style.css    # Prilagojeni CSS stili
    └── js/dashboard.js  # Dinamično pridobivanje (fetch) in upodabljanje Chart.js grafov
```

### Ključne metode obdelave podatkov (`processor.py`):
1. **Rekonstrukcija hierarhije kategorij (COICOP):** V tabeli SURS so kategorije zapisane kot vrstice brez pik (`01.1.1 KRUH...`), izdelki pa se začnejo s tremi pikami (`...Riž, beli (kg)`). Procesor samodejno preslika vsak izdelek v njegovo matično kategorijo.
2. **Izluščenje merskih enot:** Iz imena izdelka se z regularnim izrazom ločita čisto ime in merska enota (`kg`, `l`, `10 kosov`, `porcija`).
3. **Dinamično filtriranje (`get_filtered_prices`):** Filtriranje po prehranski skupini, letu in iskalnem nizu po imenu.
4. **Agregacija inflacije (`get_inflation_leaders`):** Izračun odstotne rasti cen med letoma 2018 in 2025 za rangiranje najbolj podraženih živil.
5. **Anti-Wolt Primerjalnik (`get_anti_wolt_comparison`):** Izračun dejanskega stroška domače priprave obroka (testenine, pica, kosilo, kava) in neposredna primerjava s ceno gostinske porcije / dostave iz iste uradne statistike SURS.

---

## 4. REST API Končne točke (`app.py`)

Vse končne točke vračajo podatke v standardnem formatu **JSON**:

| Metoda | Endpoint | Opis |
| :--- | :--- | :--- |
| `GET` | `/` | Servira glavno HTML nadzorno ploščo z grafi Chart.js. |
| `GET` | `/api/metadata` | Vrne metapodatke tabele, nabor let in seznam prehranskih kategorij. |
| `GET` | `/api/prices` | Vrne filtrirane cene (parametri: `year`, `category`, `search`). |
| `GET` | `/api/history` | Vrne časovno vrsto cen za izdelek (parameter: `product`). |
| `GET` | `/api/inflation` | Vrne živila z največjo stopnjo podražitve (parametri: `start_year`, `end_year`, `limit`). |
| `GET` | `/api/comparison` | Vrne podatke za Anti-Wolt primerjalnik domače hrane proti dostavi. |
| `POST` | `/api/refresh` | Prisilno osveži `.px` datoteko neposredno iz strežnika SURS/OPSI. |

---

## 5. Grafična vizualizacija (Chart.js) in dinamičnost

V skladu z navodili naloge so vsi grafični prikazi **dinamični**:
- Ob prvem obisku strani (`DOMContentLoaded`) skripta `static/js/dashboard.js` izvede asinhrone zahteve (`fetch()`) na API končne točke.
- Podatki se obdelajo na strežniku v Pythonu s `pyaxis` ter pošljejo brskalniku, kjer **Chart.js** izriše:
  1. **Anti-Wolt primerjalni stolpčni graf:** Domača priprava vs. gostinska hrana/dostava s prikazom prihrankov.
  2. **Časovni trend (Line Chart):** Gibanje cen posameznega živila od 2018 do 2025 z možnostjo interaktivnega preklopa.
  3. **Vodoravni stolpčni graf (Horizontal Bar):** Rekorderji podražitev v Sloveniji v obdobju 2018–2025.
  4. **Interaktivna tabela:** Iskanje in filtriranje v realnem času brez osveževanja strani (live search & filter).

---

## 6. Navodila za namestitev in zagon

### 6.1 Namestitev odvisnosti
V korenski mapi projekta namestite zahtevane pakete:
```bash
pip install -r food_prices/requirements.txt
```

### 6.2 Zagon spletne aplikacije
Aplikacijo zaženete z ukazom:
```bash
python -m food_prices.app
```
ali neposredno:
```bash
python food_prices/app.py
```

Aplikacija se zažene na lokalnem naslovu:
👉 **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

Odprite povezavo v brskalniku za ogled dinamične nadzorne plošče.


