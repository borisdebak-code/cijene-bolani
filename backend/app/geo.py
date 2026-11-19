"""Normalizacija gradova.

Trgovci isti grad pišu različito ("VELIKA GORICA", "Velika Gorica",
"V.GORICA", "VELIKA_GORICA") – a filtar po gradu radi samo ako je to
jedan grad. Prednost ima poštanski broj, zatim poznati nazivi i kratice.
"""
from __future__ import annotations

import re

from .adapters.csvutil import strip_accents

POSTCODE_CITY: dict[str, str] = {
    # Zagreb i okolica
    "10000": "Zagreb", "10010": "Zagreb", "10020": "Zagreb", "10040": "Zagreb",
    "10090": "Zagreb", "10110": "Zagreb", "10360": "Sesvete",
    "10410": "Velika Gorica", "10290": "Zaprešić", "10430": "Samobor",
    "10450": "Jastrebarsko", "10370": "Dugo Selo", "10310": "Ivanić-Grad",
    "10340": "Vrbovec", "10380": "Sveti Ivan Zelina",
    # Dalmacija
    "21000": "Split", "21210": "Solin", "21220": "Trogir", "21300": "Makarska",
    "21310": "Omiš", "21230": "Sinj", "21260": "Imotski", "22000": "Šibenik",
    "23000": "Zadar", "20000": "Dubrovnik",
    # Istra i Kvarner
    "51000": "Rijeka", "51410": "Opatija", "51260": "Crikvenica", "51500": "Krk",
    "52100": "Pula", "52440": "Poreč", "52210": "Rovinj", "52470": "Umag",
    # Slavonija
    "31000": "Osijek", "32000": "Vukovar", "32100": "Vinkovci",
    "35000": "Slavonski Brod", "34000": "Požega", "33000": "Virovitica",
    # sjever i središnja Hrvatska
    "40000": "Čakovec", "40323": "Prelog", "42000": "Varaždin", "43000": "Bjelovar",
    "44000": "Sisak", "47000": "Karlovac", "48000": "Koprivnica", "49000": "Krapina",
    "53000": "Gospić",
}

KNOWN_CITIES: set[str] = set(POSTCODE_CITY.values())

# kratice i česti zapisi -> službeni naziv
_ALIASES = {
    "v gorica": "Velika Gorica",
    "vel gorica": "Velika Gorica",
    "sl brod": "Slavonski Brod",
    "ivanic grad": "Ivanić-Grad",
    "sv ivan zelina": "Sveti Ivan Zelina",
}


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(s).lower()).strip()


_BY_KEY = {_key(c): c for c in KNOWN_CITIES} | _ALIASES


def normalize_city(city: str | None, postcode: str | None = None) -> str | None:
    if postcode and postcode.strip() in POSTCODE_CITY:
        return POSTCODE_CITY[postcode.strip()]
    if not city:
        return None
    k = _key(city)
    if k in _BY_KEY:
        return _BY_KEY[k]
    # nepoznat grad: barem ujednači velika/mala slova
    return " ".join(w[:1].upper() + w[1:] for w in city.strip().lower().split())


def find_city(tokens: list[str]) -> tuple[str, int, int] | None:
    """Pronađi poznati grad u nizu riječi (npr. iz imena datoteke).
    Vraća (grad, početak, kraj) – najduže poklapanje, prvo s lijeva."""
    keys = [_key(t) for t in tokens]
    for i in range(len(tokens)):
        for j in range(min(len(tokens), i + 4), i, -1):
            k = " ".join(keys[i:j])
            if k in _BY_KEY:
                return _BY_KEY[k], i, j
    return None
