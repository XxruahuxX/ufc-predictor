"""Emparejar peleadores entre fuentes que no comparten ids.

Wikipedia no usa los ids de UFCStats: solo da nombres. Para saber si
"Jiří Procházka" es el "Jiri Prochazka" de la base de datos se comparan
nombres normalizados (ver names.py).
"""
from collections import defaultdict
from difflib import SequenceMatcher, get_close_matches
from typing import Iterable

from app.db.models import Fighter
from app.ingestion.names import SUFFIXES, name_keys, normalize_name


def is_catchweight(weight_class: str) -> bool:
    """Peso pactado o abierto: no es la categoría habitual del peleador."""
    lower = weight_class.lower()
    return "catch" in lower or "open" in lower


def same_weight(a: str | None, b: str | None) -> bool:
    """'Women's Flyweight' y 'womens flyweight' son la misma categoría."""
    return bool(a and b) and normalize_name(a) == normalize_name(b)


class FighterIndex:
    """Busca peleadores por nombre, en dos pasos.

    1. Nombre exacto (sin tildes ni mayúsculas) o un alias.
    2. Si no hay nadie, variantes del nombre: sin "Jr.", sin espacios
       o con las palabras en otro orden.
    """

    def __init__(self, fighters: Iterable[Fighter] = ()):
        self.exact: dict[str, list[Fighter]] = defaultdict(list)
        self.loose: dict[str, list[Fighter]] = defaultdict(list)
        for fighter in fighters:
            self.add(fighter)

    def add(self, fighter: Fighter) -> None:
        names = [fighter.name, *(a.alias for a in fighter.aliases)]
        for name in names:
            self.exact[normalize_name(name)].append(fighter)
            for key in name_keys(name):
                self.loose[key].append(fighter)

    def remove(self, fighter: Fighter) -> None:
        for table in (self.exact, self.loose):
            for fighters in table.values():
                while fighter in fighters:
                    fighters.remove(fighter)

    def find(self, name: str, weight_class: str | None = None) -> list[Fighter]:
        """Peleadores que pueden llamarse así. Lo normal es 0 o 1.

        Si hay varios (dos peleadores con el mismo nombre), se quedan los
        de la misma categoría y, entre ellos, los que ya tienen id de
        UFCStats. Si aun así quedan varios, se devuelven todos: quien
        llama decide, porque adivinar sería peor.
        """
        for table, keys in ((self.exact, {normalize_name(name)}), (self.loose, name_keys(name))):
            found = list(dict.fromkeys(f for key in keys for f in table.get(key, [])))
            if not found:
                continue
            if len(found) > 1:
                same = [f for f in found if same_weight(f.weight_class, weight_class)]
                found = same or found
            if len(found) > 1:
                known = [f for f in found if f.ufcstats_id]
                found = known or found
            return found
        return []

    def similar(self, name: str, n: int = 2) -> list[str]:
        """Nombres de peleadores del CSV parecidos a este, de más a menos.

        'Abusupiyan Magomedov' -> ['Abus Magomedov']. Es solo una pista para
        el resumen: no une a nadie. Se comparan las palabras ordenadas, así
        no cuenta si el apellido va delante ('Ko Seok-hyeon' / 'SeokHyun Ko').
        Además, el apellido tiene que parecerse: 'Nick Galanti' no sugiere
        'Nick Agallar' aunque el nombre completo se parezca.
        """
        names = {sorted_words(f.name): f.name
                 for fighters in self.exact.values() for f in fighters if f.ufcstats_id}
        close = get_close_matches(sorted_words(name), list(names), 5, cutoff=0.75)
        return [names[key] for key in close if same_surname(name, names[key])][:n]


def words(name: str) -> list[str]:
    """'Ko Seok-hyeon Jr.' -> ['ko', 'seokhyeon']: sin guiones ni sufijos."""
    return [w for w in normalize_name(name.replace("-", "")).split() if w not in SUFFIXES]


def sorted_words(name: str) -> str:
    """'Ko Seok-hyeon' -> 'ko seokhyeon'."""
    return " ".join(sorted(words(name)))


def same_surname(a: str, b: str) -> bool:
    """La última palabra de cada nombre se parece a alguna del otro.

    'Timothy Cuamba' / 'Timmy Cuamba': sí. 'Alik Lorenz' / 'Lorenz Larkin':
    no, porque 'Larkin' no se parece a nada de 'Alik Lorenz'. Mirar las
    dos direcciones vale también para los nombres coreanos, que llevan el
    apellido delante en Wikipedia ('Ko Seok-hyeon' / 'SeokHyun Ko').
    """
    wa, wb = words(a), words(b)

    def fits(word: str, others: list[str]) -> bool:
        return any(SequenceMatcher(None, word, other).ratio() >= 0.8 for other in others)

    return bool(wa and wb) and fits(wa[-1], wb) and fits(wb[-1], wa)
