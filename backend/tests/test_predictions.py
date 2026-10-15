"""Tests de la importación de predicciones externas desde un CSV."""
from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.models import Event, ExternalPrediction, Fight, Fighter, FighterAlias, Source
from app.ingestion.predictions import import_predictions, parse_confidence, RowError

CABECERA = "fuente,tipo,fecha_evento,ganador,rival,confianza,metodo,publicado,url"


@pytest.fixture
def cartelera(session):
    """UFC 333 el 24 de octubre: Volkanovski (A) vs. Evloev (B) y Prochazka (A) vs. Rountree (B)."""
    event = Event(name="UFC 333: Volkanovski vs. Evloev", date=date(2026, 10, 24))
    nombres = ["Alexander Volkanovski", "Movsar Evloev", "Jiri Prochazka", "Khalil Rountree Jr."]
    f = [Fighter(name=n, ufcstats_id=f"f{i}") for i, n in enumerate(nombres)]
    f[1].aliases.append(FighterAlias(alias="Movsar Evloyev"))
    session.add_all([event, *f])
    session.add_all([Fight(event=event, fighter_a=f[0], fighter_b=f[1], bout_order=1),
                     Fight(event=event, fighter_a=f[2], fighter_b=f[3], bout_order=2)])
    session.commit()
    return {fighter.name: fighter for fighter in f}


def importar(session, *filas):
    report = import_predictions(session, [CABECERA, *filas])
    session.commit()
    return report


def prediccion(session, fuente, ganador_de_la_pelea):
    return session.scalar(
        select(ExternalPrediction).join(Source).join(Fight).join(Fighter, Fight.fighter_a)
        .where(Source.name == fuente, Fighter.name == ganador_de_la_pelea))


def test_importa_y_crea_las_fuentes(session, cartelera):
    report = importar(
        session,
        "MMA Junkie,media,2026-10-24,Movsar Evloev,Alexander Volkanovski,65 %,KO,2026-10-20,https://ejemplo.com/picks",
        "MMA Junkie,media,2026-10-24,Jiří Procházka,Khalil Rountree,0.7,,,",
        "Tipster X,tipster,2026-10-24,Alexander Volkanovski,Movsar Evloev,,decision,,",
    )
    assert (report.new, report.updated, report.problems) == (3, 0, [])
    assert report.sources_new == ["MMA Junkie", "Tipster X"]

    p = prediccion(session, "MMA Junkie", "Alexander Volkanovski")
    assert p.predicted_winner_id == cartelera["Movsar Evloev"].id  # eligió a B
    assert (p.confidence, p.predicted_method, p.url) == (0.65, "KO/TKO", "https://ejemplo.com/picks")
    assert p.published_at.date() == date(2026, 10, 20)

    # Tildes y "Jr." no impiden encontrar la pelea
    p = prediccion(session, "MMA Junkie", "Jiri Prochazka")
    assert (p.predicted_winner_id, p.confidence) == (cartelera["Jiri Prochazka"].id, 0.7)
    assert p.published_at.date() == date(2026, 10, 24)  # sin fecha: el día del evento

    # Sin confianza: la de la fuente (0.6 por defecto)
    p = prediccion(session, "Tipster X", "Alexander Volkanovski")
    assert (p.confidence, p.predicted_method) == (0.6, "DEC")
    assert session.scalar(select(Source).where(Source.name == "Tipster X")).kind == "tipster"


def test_un_dia_de_diferencia_y_alias(session, cartelera):
    # En América el evento es el 24; en Europa ya es día 25. Y un alias.
    report = importar(session, "Foro,forum,2026-10-25,Movsar Evloyev,Volkanovski Alexander,60,,,")
    assert report.problems == []
    assert session.scalar(select(ExternalPrediction)).predicted_winner_id == cartelera["Movsar Evloev"].id


def test_cambiar_de_opinion_sustituye_la_prediccion(session, cartelera):
    importar(session, "Tipster X,tipster,2026-10-24,Movsar Evloev,Alexander Volkanovski,0.8,,,")
    report = importar(session, "Tipster X,tipster,2026-10-24,Alexander Volkanovski,Movsar Evloev,0.55,,,")
    assert (report.new, report.updated) == (0, 1)
    p = session.scalar(select(ExternalPrediction))
    assert (p.predicted_winner_id, p.confidence) == (cartelera["Alexander Volkanovski"].id, 0.55)
    assert session.scalar(select(func.count()).select_from(ExternalPrediction)) == 1


def test_filas_con_errores(session, cartelera):
    report = importar(
        session,
        "A,media,2026-10-24,Movsar Evloev,Alexander Volkanovski,0.4,,,",   # confianza < 50 %
        "B,revista,2026-10-24,Movsar Evloev,Alexander Volkanovski,,,,",    # tipo no válido
        "C,media,2026-11-01,Movsar Evloev,Alexander Volkanovski,,,,",      # no hay evento
        "D,media,2026-10-24,Movsar Evloev,Jiri Prochazka,,,,",            # no pelean entre ellos
        "E,media,24/10/2026,Movsar Evloev,Alexander Volkanovski,,,,",      # fecha mal escrita
        "F,media,2026-10-24,,Alexander Volkanovski,,,,",                   # falta el ganador
        "G,media,2026-10-24,Movsar Evloev,Alexander Volkanovski,,por puntos,,",  # método
    )
    assert report.new == 0
    assert [p.split(":")[0] for p in report.problems] == [f"línea {n}" for n in range(2, 9)]
    assert "50 y 100 %" in report.problems[0]
    assert "tipo no válido" in report.problems[1]
    assert "no hay ningún evento el 2026-11-01" in report.problems[2]
    assert "no hay ninguna pelea Movsar Evloev vs. Jiri Prochazka" in report.problems[3]
    assert "faltan columnas: ganador" in report.problems[5]
    # Una fila con error no deja una fuente a medias
    assert session.scalar(select(func.count()).select_from(Source)) == 0


def test_cabecera_con_mayusculas_y_espacios(session, cartelera):
    filas = ["Fuente,Tipo,Fecha evento,Ganador,Rival", "Foro,forum,2026-10-24,Movsar Evloev,Alexander Volkanovski"]
    report = import_predictions(session, filas)
    assert (report.new, report.problems) == (1, [])


def test_formatos_de_confianza():
    assert parse_confidence("0.65") == 0.65
    assert parse_confidence("65") == 0.65
    assert parse_confidence("65 %") == 0.65
    assert parse_confidence("0,7") == 0.7
    assert parse_confidence("") is None
    with pytest.raises(RowError):
        parse_confidence("mucha")
