"""Normalización de nombres de peleadores.

Cada fuente escribe los nombres a su manera: "José Aldo", "Jose Aldo",
"JOSE ALDO". Para compararlos los pasamos todos a una forma común.
"""
import re
import unicodedata

# Letras que no se descomponen en "letra + tilde" y hay que traducir a mano
SPECIAL_LETTERS = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
                                 "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i"})


def normalize_name(name: str) -> str:
    """'José  Aldo Jr.' -> 'jose aldo jr'"""
    # Quita tildes: "é" se descompone en "e" + tilde, y nos quedamos con la "e"
    text = unicodedata.normalize("NFKD", name.translate(SPECIAL_LETTERS))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())  # fuera puntos, guiones, apóstrofes
    return " ".join(text.split())


# Sufijos que unas fuentes ponen y otras no: "Khalil Rountree Jr." / "Khalil Rountree"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def name_keys(name: str) -> set[str]:
    """Variantes de un nombre para emparejar fuentes que lo escriben distinto.

    'Khalil Rountree Jr.' -> {'khalil rountree', 'khalilrountree'}
    Coinciden: con y sin "Jr.", 'Da Woon Jung' y 'Dawoon Jung',
    'Zhang Weili' y 'Weili Zhang' (orden de las palabras).
    """
    words = [w for w in normalize_name(name).split() if w not in SUFFIXES]
    if not words:
        return set()
    return {" ".join(words), "".join(words), " ".join(sorted(words))}
