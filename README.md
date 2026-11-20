# Cijene BoLaNI

Usporedba cijena proizvoda u trgovinama u Hrvatskoj. Podaci dolaze iz **službenih
dnevnih cjenika** (CSV/XML) koje trgovci moraju objavljivati (Odluka NN 75/2025).
HTML stranice proizvoda se ne scrapaju.

**Trgovci:** Konzum, Lidl, Kaufland, Spar, Interspar, Plodine.

**Za korisnike** (javni način, `PUBLIC_MODE=true`):
- pretraga s padajućim izbornikom konkretnih proizvoda (naziv ili barkod); kad je upit
  općenit (npr. "svježe mlijeko"), ispod rezultata se nude pitanja za sužavanje izbora:
  zapremnina/težina, proizvođač, postotak (mliječna mast, alkohol ili udio kakaa – prema
  vrsti proizvoda) i vrsta (bez laktoze, trajno/UHT, svježe, bio, light...). Pitanje se
  prikazuje samo ako dijeli rezultate; atributi se izvlače iz naziva (`normalize.product_attrs`)
- akcije: vrsta akcije (akcijska prodaja, sezonsko sniženje, rasprodaja), u koliko
  poslovnica vrijedi i – ako je trgovac objavi – datum do kojeg vrijedi; cijena uz karticu
  vjernosti (MultiPlus, Kaufland Card, Lidl Plus...) prikazuje se ljubičasto kao moguća
  niža cijena. **Napomena:** u studenom 2026. nijedan od podržanih cjenika ne sadrži datum
  trajanja akcije ni cijenu s karticom; parser ih prepoznaje čim se pojave (stupci
  "vrijedi do", "trajanje", "kartica", "MultiPlus"...)
- usporedba po trgovcima: uobičajena cijena (medijan po poslovnicama, s akcijama), raspon, akcije
- graf povijesti cijene
- **popis za kupnju** (gumb u donjem desnom kutu): za svaki proizvod najjeftinija trgovina,
  popis podijeljen po trgovinama (s gumbom za kopiranje svakog popisa) i usporedba
  "sve u jednoj trgovini". Popis se čuva samo u korisnikovom pregledniku.

Ništa drugo korisnik ne može mijenjati; administracija (`/api/status`, `/docs`) je u javnom
načinu isključena.

## Izvori podataka

| trgovac | izvor | napomena |
|---|---|---|
| Konzum, Lidl, Kaufland, Spar, Interspar | `cijenedev` adapter (dnevna arhiva api.cijene.dev) | Spar hipermarketi = Interspar |
| Plodine | `plodine` adapter (izravno s plodine.hr) | nije u cijene.dev arhivi |

Izravni adapteri za Konzum, Kaufland i Spar/Interspar rade i provjereni su na stvarnim
datotekama (`tests/fixtures/*/real/`); Lidlova stranica trenutno ne objavljuje linkove na
aktualne cjenike, pa Lidl ide preko cijene.dev. Izvori se biraju s `INGEST_CHAINS` i
`CIJENEDEV_CHAINS` u `.env`.

## Pokretanje (server, npr. VPS)

```bash
cp .env.example .env          # promijeni POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec worker python -m app.worker once            # prvo preuzimanje odmah
docker compose exec worker python /srv/scripts/backfill_daily.py --days 30   # povijest za graf
```

- web: http://<server>:8000 (za javni pristup stavi ispred nginx/Caddy s HTTPS-om)
- worker preuzima cjenike svaki dan u **08:30 (Europe/Zagreb)**, a do 14:30 svaki sat ponovno
  pokušava za trgovce čiji cjenik još nije bio objavljen

```bash
docker compose exec worker python -m app.worker once --chains plodine --date 2026-11-18
docker compose exec worker python -m app.worker load plodine /data/raw/cjenik.zip
docker compose exec worker python -m app.worker list
docker compose exec api pytest -q
```

## Statična verzija (bez servera)

```bash
cd backend && python ../scripts/export_static.py ../site
```

`site/` je potpuna aplikacija u statičnim datotekama (indeks za pretragu + podaci po
proizvodu u 128 dijelova koji se učitavaju na zahtjev). Može se postaviti na bilo koji
statični hosting. S `--artifact` se izvozi kao claude.ai stranica.

## Arhitektura

```
backend/app/
  adapters/            ← jedan trgovac = jedna datoteka
    base.py            Adapter: discover(day) → download(url) → parse(file)
    csvutil.py         encoding (UTF-8/BOM/Windows-1250), delimiter, decimalni zarez,
                       mapiranje zaglavlja po ključnim riječima
    konzum.py lidl.py plodine.py kaufland.py spar.py cijenedev.py
                       (spar.py puni dva trgovca: `spar` i `interspar`)
  geo.py               gradovi: poštanski broj → grad, kratice (V.GORICA → Velika Gorica)
  normalize.py         EAN (checksum, UPC→EAN-13, odbacuje interne 2xx kodove),
                       količina → g/ml/kom, tekst za pretragu
  services/ingest.py   paket poslovnica istog trgovca → COPY u staging → chain_product →
                       product → intervali cijena → dnevni sažetak (chain_daily)
  services/matching.py spajanje: EAN, pa fuzzy (pg_trgm + rapidfuzz) uz istu količinu i marku
  api/routes.py        /api/suggest, /api/products/{id}/summary (javno), /api/search,
                       /api/products/{id}/prices|history, /api/cities, /api/status (admin)
  worker.py            APScheduler (dnevno 08:30) + CLI
frontend/              statični responzivni web (vanilla JS, Chart.js lokalno)
```

### Model podataka

| tablica | sadržaj |
|---|---|
| `chain` | trgovac, `last_day` = datum zadnjeg cjenika |
| `store` | poslovnica: šifra, tip, adresa, poštanski broj, grad |
| `product` | kanonski proizvod (EAN, naziv, marka, količina, jedinica, kategorija) |
| `chain_product` | artikl kod trgovca (njegova šifra) → `product_id`, `match_method` = ean/fuzzy/new |
| `price` | MPC, cijena/JM, akcijska cijena, najniža 30 d, sidrena; **interval** `valid_from`–`last_seen` |
| `chain_daily` | dnevni sažetak po proizvodu i trgovcu: medijan, min, max, broj poslovnica, akcije |
| `ingest_run`, `ingest_error` | log po trgovcu/danu i po datoteci |

**Povijest se ne prepisuje.** Ako je cijena ista kao jučer, produžuje se `last_seen`;
ako se promijenila, stari red se zatvara (`is_current=false`), a dodaje se novi. Ponovno
pokretanje za isti dan ne mijenja ništa. Tako je baza 20–50× manja nego s jednim redom po
danu × poslovnici × proizvodu (samo Konzum je ~10 M takvih redova dnevno).

### Kad trgovac promijeni format

- Stupci se mapiraju po ključnim riječima (`naziv`, `maloprodajn`, `posebn`, `najniz`,
  `sidren`, `barkod`…), pa sitne promjene (velika/mala slova, `_`, dijakritika, redoslijed) ne smetaju.
- Ako nedostaju obavezni stupci → `FormatChanged`, zapis u `ingest_error` s cijelim zaglavljem.
- Ako je broj redaka pao ispod 50 % prošlog uspješnog preuzimanja → status `partial` (`row_drop`).
- Greška jednog trgovca/datoteke nikad ne zaustavlja ostale.
- Sve je vidljivo na `/#/status` i `GET /api/status`, i u logovima s prefiksom `[konzum]`, `[lidl]`…

### Gradovi

Popis gradova u aplikaciji puni se iz poslovnica, pa grad (npr. Velika Gorica) postaje
dostupan čim neki trgovac ima poslovnicu u njemu. Da bi filtar radio, isti grad mora biti
zapisan isto kod svih trgovaca: `app/geo.py` prvo gleda poštanski broj (10410 → Velika Gorica),
zatim poznate nazive i kratice. Novi grad dodaje se jednim retkom u `POSTCODE_CITY`.

### Dodavanje trgovca

Nova datoteka `backend/app/adapters/<trgovac>.py`:

```python
class SparAdapter(Adapter):
    code = "spar"
    name = "Spar"

    def discover(self, day):          # URL-ovi cjenika za taj dan
        ...
    @staticmethod
    def store_from_filename(name):    # poslovnica iz imena datoteke
        ...
    def parse(self, raw):
        rows, warnings, enc = parse_price_csv(raw.data)
        yield StorePrices(self.code, self.store_from_filename(raw.name), rows, raw.name, enc, warnings)
```

Registar ga pronalazi automatski. Dodaj primjerak datoteke u `tests/fixtures/<trgovac>/` i test.
Za XML cjenike (npr. neki trgovci) dovoljno je u `parse()` umjesto `parse_price_csv` koristiti XML parser
i vraćati iste `PriceRow` objekte.

## Testovi

```bash
cd backend && pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/cijene_test pytest -q
```

- `test_parsing.py` – decimalni brojevi, encoding, zaglavlja svih trgovaca, EAN, količine
- `test_adapters.py` – parsiranje fixturea, imena datoteka → poslovnice, `discover()` uz lažni HTTP
- `test_ingest_api.py` – spajanje proizvoda, intervali povijesti, idempotentnost, logiranje grešaka, API
- `test_real_samples.py` – stvarne datoteke trgovaca (vidi dolje)

> **Napomena o fixtureima:** datoteke u `tests/fixtures/` izrađene su prema formatu stvarnih cjenika
> (zaglavlja, imena datoteka, encoding, delimiteri), ali **nisu preuzete** od trgovaca jer razvojno
> okruženje nije imalo pristup njihovim stranicama. Na mreži s pristupom pokreni
> `python scripts/fetch_samples.py` – spremit će po 2 stvarne datoteke po trgovcu u
> `tests/fixtures/<trgovac>/real/`, a `test_real_samples.py` ih automatski testira.
> Također provjeri URL-ove u `discover()` metodama – stranice trgovaca se mijenjaju.

## Sljedeće iteracije

1. Ostali trgovci (Tommy, Studenac, Eurospin, KTC, NTL, Ribola, Metro, DM, Boso, Trgovina Krk, Žabac)
2. "Trenutne akcije" po trgovcu i kategoriji (podaci već postoje: `special_price`, `lowest_30d`, prethodni interval)
3. Praćenje proizvoda + obavijesti (email / web push) kad krene akcija ili cijena padne ispod praga
4. Košarica – najjeftiniji dućan za listu proizvoda
5. Particioniranje tablice `price` po mjesecu, Alembic migracije, ručno potvrđivanje fuzzy spajanja
