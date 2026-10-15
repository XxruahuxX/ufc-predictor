"""Tests de la comparación de nombres entre fuentes."""
from app.db.models import Fighter
from app.ingestion.matching import FighterIndex, is_catchweight, same_weight
from app.ingestion.names import name_keys


def peleador(nombre, categoria=None, ufcstats_id=None):
    return Fighter(name=nombre, weight_class=categoria, ufcstats_id=ufcstats_id)


def test_variantes_de_un_nombre():
    assert name_keys("Khalil Rountree Jr.") == {"khalil rountree", "khalilrountree"}
    assert name_keys("Da Woon Jung") & name_keys("Dawoon Jung")  # con y sin espacio
    assert name_keys("Zhang Weili") & name_keys("Weili Zhang")  # orden de las palabras
    assert not name_keys("Bruno Silva") & name_keys("Bruno Silvas")
    assert name_keys("Jr.") == set()


def test_busca_por_nombre_exacto_y_por_variantes():
    prochazka = peleador("Jiri Prochazka", "Light Heavyweight", "c")
    rountree = peleador("Khalil Rountree Jr.", "Light Heavyweight", "d")
    zhang = peleador("Weili Zhang", "Women's Strawweight", "e")
    index = FighterIndex([prochazka, rountree, zhang])

    assert index.find("Jiří Procházka") == [prochazka]  # tildes
    assert index.find("KHALIL ROUNTREE") == [rountree]  # sin "Jr." y en mayúsculas
    assert index.find("Zhang Weili") == [zhang]
    assert index.find("Conor McGregor") == []


def test_dos_peleadores_con_el_mismo_nombre():
    medio = peleador("Bruno Silva", "Middleweight", "a")
    mosca = peleador("Bruno Silva", "Flyweight", "b")
    index = FighterIndex([medio, mosca])

    assert index.find("Bruno Silva", "Flyweight") == [mosca]  # decide la categoría
    assert index.find("Bruno Silva") == [medio, mosca]  # sin categoría no se adivina
    assert index.find("Bruno Silva", "Lightweight") == [medio, mosca]


def test_prefiere_el_que_tiene_id_de_ufcstats():
    viejo = peleador("Joe Doe", "Lightweight", "f")
    creado_por_wikipedia = peleador("Joe Doe", "Lightweight")
    assert FighterIndex([viejo, creado_por_wikipedia]).find("Joe Doe") == [viejo]


def test_el_exacto_gana_a_las_variantes():
    exacto = peleador("Weili Zhang", ufcstats_id="1")
    al_reves = peleador("Zhang Weili", ufcstats_id="2")
    assert FighterIndex([exacto, al_reves]).find("Weili Zhang") == [exacto]


def test_quitar_del_indice():
    joe = peleador("Joe Doe")
    index = FighterIndex([joe])
    index.remove(joe)
    assert index.find("Joe Doe") == []


def test_categorias():
    assert is_catchweight("Catchweight (150 lb)")
    assert is_catchweight("Catch Weight")
    assert is_catchweight("Open Weight")
    assert not is_catchweight("Lightweight")
    assert same_weight("Women's Flyweight", "WOMEN’S FLYWEIGHT")  # apóstrofo tipográfico
    assert not same_weight(None, "Flyweight")
