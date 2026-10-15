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
