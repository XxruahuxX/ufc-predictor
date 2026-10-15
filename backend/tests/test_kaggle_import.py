"""Tests del importador del CSV de Kaggle.

El CSV de prueba se genera aquí con las mismas columnas que el real
(solo las que se usan y algunas que se ignoran). Las dos primeras filas
copian datos reales del CSV: UFC Fight Night: Allen vs. Duncan.
"""
import csv
from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.models import Event, Fight, Fighter, FightResult, FightStats, Odds
from app.ingestion.kaggle_import import (
    CsvFighter,
    WrongCsvError,
    decimal_odds,
    import_csv,
    normalize_result,
    print_report,
    winner_index,
)

UFC = "http://ufcstats.com"
HOY = date(2026, 10, 15)

# (id, nombre, fecha) de cada evento
ALLEN_DUNCAN = ("7f907f907f907f91", "UFC Fight Night: Allen vs. Duncan", "2026-10-10")
UFC_290 = ("0b0b0b0b0b0b0b01", "UFC 290: Volkanovski vs. Rodriguez", "2023-07-08")
UFC_2 = ("0a0a0a0a0a0a0a01", "UFC 2: No Way Out", "1994-03-11")
PROXIMO = ("0c0c0c0c0c0c0c01", "UFC 330", "2026-11-08")

ALLEN, DUNCAN = "b0b0b0b0b0b0b0b1", "a0a0a0a0a0a0a0a1"  # Allen gana pero queda como B
HERBERT, CAMILO = "4040404040404041", "7070707070707071"  # Herbert gana y queda como A

SIN_ESTADISTICAS = {k: "" for k in (
    "knockdowns", "total_strikes_att", "total_strikes_succ", "sig_strikes_att",
    "sig_strikes_succ", "takedown_att", "takedown_succ", "submission_att", "reversals",
    "ctrl_time_sec",
)}


def peleador(n: int, nombre: str, fid: str, **datos) -> dict:
    """Columnas f_1_* o f_2_* de un peleador."""
    nombre_pila, _, apellido = nombre.partition(" ")
    columnas = {
        "name": nombre, "url": f"{UFC}/fighter-details/{fid}",
        "fighter_f_name": nombre_pila, "fighter_l_name": apellido, "fighter_nickname": "",
        "fighter_height_cm": "180.34", "fighter_weight_lbs": "155.0", "fighter_reach_cm": "182.88",
        "fighter_stance": "Orthodox", "fighter_dob": "1990-01-01",
        "fighter_w": "10", "fighter_l": "2", "fighter_d": "0", "fighter_nc_dq": "",
        "fighter_SlpM": "3.50", "fighter_Str_Acc": "0.45", "fighter_url": f"{UFC}/fighter-details/{fid}",
        "knockdowns": "0", "total_strikes_att": "100", "total_strikes_succ": "50",
        "sig_strikes_att": "80", "sig_strikes_succ": "40", "takedown_att": "2", "takedown_succ": "1",
        "submission_att": "0", "reversals": "0", "ctrl_time_sec": "60",
        "r1_sig_pct": "50.0", "ranking": "", "odds": "", "ko_odds": "", "sub_odds": "",
    }
    columnas.update(datos)
    return {f"f_{n}_{k}": v for k, v in columnas.items()}


def pelea(fight_id: str, evento: tuple, f1: dict, f2: dict, **datos) -> dict:
    """Una fila del CSV. Por defecto gana f_1 por decisión unánime."""
    fila = {
        "event_name": evento[1], "referee": "Herb Dean", "winner": f1["f_1_name"],
        "num_rounds": "3", "title_fight": "False", "weight_class": "Lightweight", "gender": "M",
        "result": "Decision - Unanimous", "result_details": "Mike Bell 28 - 29. Eric Colon 28 - 29.",
        "finish_round": "3", "finish_time": "5:00", "fight_url": f"{UFC}/fight-details/{fight_id}",
        "event_date": evento[2], "event_city": "Las Vegas", "event_state": "Nevada",
        "event_country": "USA", "event_url": f"{UFC}/event-details/{evento[0]}",
    }
    fila.update(f1)
    fila.update(f2)
    fila.update(datos)
    return fila


def filas_de_prueba() -> list[dict]:
    herbert_camilo = pelea(
        "295f295f295f2951", ALLEN_DUNCAN,
        peleador(1, "Jai Herbert", HERBERT, odds="1.80", total_strikes_att="194",
                 total_strikes_succ="80", sig_strikes_att="157", sig_strikes_succ="54",
                 takedown_att="1", takedown_succ="0", reversals="1", ctrl_time_sec="175"),
        peleador(2, "Matheus Camilo", CAMILO, odds="2.05"),
    )
    empate = pelea("c0c0c0c0c0c0c0c1", ALLEN_DUNCAN,
                   peleador(1, "Charlie Three", "c1c1c1c1c1c1c1c1"),
                   peleador(2, "Delta Four", "c2c2c2c2c2c2c2c2"),
                   winner="", result="Decision - Split")
    return [
        # Fila real: el CSV pone primero al ganador (Allen)
        pelea(
            "7db07db07db07db1", ALLEN_DUNCAN,
            peleador(1, "Brendan Allen", ALLEN, fighter_nickname="All In",
                     fighter_height_cm="187.96", fighter_reach_cm="190.50", fighter_dob="1995-12-28",
                     fighter_w="28", fighter_l="7", total_strikes_att="150", total_strikes_succ="91",
                     sig_strikes_att="107", sig_strikes_succ="51", takedown_att="11",
                     takedown_succ="4", submission_att="2", reversals="1", ctrl_time_sec="519",
                     ranking="4", odds="-150"),
            peleador(2, "Christian Leroy Duncan", DUNCAN, fighter_stance="Switch", fighter_nc_dq="1 NC",
                     total_strikes_att="212", total_strikes_succ="126", sig_strikes_att="189",
                     sig_strikes_succ="106", takedown_att="0", takedown_succ="0",
                     reversals="1", ctrl_time_sec="231", ranking="10", odds="+130"),
            num_rounds="5", weight_class="Middleweight", result="Submission",
            result_details="Rear Naked Choke", finish_round="5", finish_time="2:46",
        ),
        herbert_camilo,
        empate,
        pelea("d0d0d0d0d0d0d0d1", ALLEN_DUNCAN,
              peleador(1, "Echo Five", "d1d1d1d1d1d1d1d1"),
              peleador(2, "Foxtrot Six", "d2d2d2d2d2d2d2d2"),
              winner="", result="Overturned", result_details="", finish_round="1",
              finish_time="3:00"),
        pelea("e0e0e0e0e0e0e0e1", ALLEN_DUNCAN,
              peleador(1, "Golf Seven", "e1e1e1e1e1e1e1e1"),
              peleador(2, "Hotel Eight", "e2e2e2e2e2e2e2e2"),
              result="KO/TKO", result_details="Punch to Head At Distance", finish_round="1",
              finish_time="0:45"),
        pelea("f0f0f0f0f0f0f0f1", ALLEN_DUNCAN,
              peleador(1, "India Nine", "f1f1f1f1f1f1f1f1"),
              peleador(2, "Juliet Ten", "f2f2f2f2f2f2f2f2"),
              result="TKO - Doctor's Stoppage", result_details="", finish_round="2"),
        # El ganador no es ninguno de los dos: no se guarda resultado
        pelea("9090909090909091", ALLEN_DUNCAN,
              peleador(1, "Kilo Eleven", "9191919191919191"),
              peleador(2, "Lima Twelve", "9292929292929292"),
              winner="Mike Thirteen"),
        # Pelea anterior de Allen en otra categoría
        pelea("2323232323232301", UFC_290,
              peleador(1, "Brendan Allen", ALLEN),
              peleador(2, "Oscar Fourteen", "2424242424242424"),
              weight_class="Welterweight", result="KO/TKO", result_details="Punches",
              finish_round="1", finish_time="4:10"),
        # UFC 2 (1994): un asalto sin límite y sin estadísticas
        pelea("1994199419941991", UFC_2,
              peleador(1, "Royce Gracie", "1111111111111111", **SIN_ESTADISTICAS),
              peleador(2, "Patrick Smith", "1212121212121212", **SIN_ESTADISTICAS),
              num_rounds="1", weight_class="Open Weight", result="Submission",
              result_details="Other - Choke", finish_round="1", finish_time="1:17"),
        # Pelea futura: sin resultado
        pelea("3030303030303031", PROXIMO,
              peleador(1, "Matheus Camilo", CAMILO, **SIN_ESTADISTICAS),
              peleador(2, "Papa Fifteen", "3131313131313131", **SIN_ESTADISTICAS),
              winner="", result="", result_details="", finish_round="", finish_time="",
              weight_class="Catch Weight"),
        herbert_camilo,  # repetida
        {**empate, "fight_url": ""},  # sin URL
    ]


def escribir_csv(path, filas, encoding="utf-8"):
    columnas = list(dict.fromkeys(k for fila in filas for k in fila))
    with path.open("w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, restval="")
        writer.writeheader()
        writer.writerows(filas)
    return path


@pytest.fixture
def csv_prueba(tmp_path):
    return escribir_csv(tmp_path / "ufc.csv", filas_de_prueba())


def contar(session, model):
    return session.scalar(select(func.count()).select_from(model))


def pelea_por_id(session, ufcstats_id):
    return session.scalar(select(Fight).where(Fight.ufcstats_id == ufcstats_id))


def peleador_por_id(session, ufcstats_id):
    return session.scalar(select(Fighter).where(Fighter.ufcstats_id == ufcstats_id))


# ---------------------------------------------------------------------------
# Importación completa
# ---------------------------------------------------------------------------

def test_importa_eventos_peleadores_y_peleas(session, csv_prueba):
    report = import_csv(session, csv_prueba, today=HOY)
    session.commit()

    assert report.rows == 12
    assert report.skipped == {"pelea repetida": 1,
                              "falta la URL de la pelea, del evento o de un peleador": 1}
    assert contar(session, Event) == 4
    assert contar(session, Fighter) == 18
    assert contar(session, Fight) == 10
    assert contar(session, FightResult) == 8
    assert report.outcomes == {"win": 6, "draw": 1, "no_contest": 1}
    assert report.problems == {"ganador no reconocido": 1}

    evento = session.scalar(select(Event).where(Event.ufcstats_id == ALLEN_DUNCAN[0]))
    assert evento.name == "UFC Fight Night: Allen vs. Duncan"
    assert evento.date == date(2026, 10, 10)
    assert evento.location == "Las Vegas, Nevada, USA"
    assert evento.status == "completed"
    proximo = session.scalar(select(Event).where(Event.ufcstats_id == PROXIMO[0]))
    assert proximo.status == "upcoming"
    assert pelea_por_id(session, "3030303030303031").status == "scheduled"


def test_esquinas_ordenadas_por_id_y_no_por_ganador(session, csv_prueba):
    import_csv(session, csv_prueba, today=HOY)
    session.commit()

    # Allen sale primero en el CSV (ganó), pero su id es mayor: queda como B
    pelea = pelea_por_id(session, "7db07db07db07db1")
    assert pelea.fighter_a.ufcstats_id == DUNCAN
    assert pelea.fighter_b.ufcstats_id == ALLEN
    assert pelea.result.winner_id == pelea.fighter_b.id

    pelea = pelea_por_id(session, "295f295f295f2951")
    assert pelea.fighter_a.ufcstats_id == HERBERT
    assert pelea.result.winner_id == pelea.fighter_a.id


def test_resultados(session, csv_prueba):
    import_csv(session, csv_prueba, today=HOY)
    session.commit()

    def resultado(fight_id):
        r = pelea_por_id(session, fight_id).result
        return (r.outcome, r.method, r.method_detail, r.end_round, r.end_time_seconds) if r else None

    assert resultado("7db07db07db07db1") == ("win", "SUB", "Rear Naked Choke", 5, 166)
    assert resultado("295f295f295f2951") == ("win", "DEC", "Unanimous", 3, 300)
    assert resultado("c0c0c0c0c0c0c0c1") == ("draw", "DEC", "Split", 3, 300)
    assert resultado("d0d0d0d0d0d0d0d1") == ("no_contest", "OTHER", "Overturned", 1, 180)
    assert resultado("e0e0e0e0e0e0e0e1") == ("win", "KO/TKO", "Punch to Head At Distance", 1, 45)
    assert resultado("f0f0f0f0f0f0f0f1") == ("win", "KO/TKO", "Doctor's Stoppage", 2, 300)
    assert resultado("9090909090909091") is None  # ganador no reconocido
    assert pelea_por_id(session, "9090909090909091").status == "completed"
    assert pelea_por_id(session, "c0c0c0c0c0c0c0c1").result.winner_id is None


def test_datos_de_la_pelea(session, csv_prueba):
    import_csv(session, csv_prueba, today=HOY)
    session.commit()

    estelar = pelea_por_id(session, "7db07db07db07db1")
    assert (estelar.scheduled_rounds, estelar.bout_order, estelar.weight_class) == (5, 1, "Middleweight")
    assert estelar.is_title_fight is False
    assert pelea_por_id(session, "295f295f295f2951").bout_order == 2
    # Formato antiguo de un asalto: se guarda como 3
    assert pelea_por_id(session, "1994199419941991").scheduled_rounds == 3


def test_estadisticas_por_pelea(session, csv_prueba):
    report = import_csv(session, csv_prueba, today=HOY)
    session.commit()

    assert report.with_stats == 8
    assert contar(session, FightStats) == 16
    pelea = pelea_por_id(session, "7db07db07db07db1")
    allen = next(s for s in pelea.stats if s.fighter_id == pelea.fighter_b.id)
    assert (allen.sig_strikes_landed, allen.sig_strikes_attempted) == (51, 107)
    assert (allen.total_strikes_landed, allen.total_strikes_attempted) == (91, 150)
    assert (allen.takedowns_landed, allen.takedowns_attempted) == (4, 11)
    assert (allen.submission_attempts, allen.control_seconds) == (2, 519)
    assert allen.fight_seconds == 4 * 300 + 166  # 2:46 del 5.º asalto
    # UFC 2 no tiene estadísticas
    assert pelea_por_id(session, "1994199419941991").stats == []


def test_ficha_del_peleador(session, csv_prueba):
    import_csv(session, csv_prueba, today=HOY)
    session.commit()

    allen = peleador_por_id(session, ALLEN)
    assert allen.name == "Brendan Allen"
    assert allen.nickname == "All In"
    assert (allen.height_cm, allen.reach_cm) == (188.0, 190.5)
    assert allen.birth_date == date(1995, 12, 28)
    assert (allen.wins, allen.losses, allen.draws, allen.no_contests) == (28, 7, 0, 0)
    assert allen.weight_class == "Middleweight"  # la de su pelea más reciente
    assert peleador_por_id(session, DUNCAN).no_contests == 1
    # La próxima pelea de Camilo es en peso pactado: se queda en Lightweight
    assert peleador_por_id(session, CAMILO).weight_class == "Lightweight"


def test_cuotas_en_decimal_y_orientadas_a_a_y_b(session, csv_prueba):
    report = import_csv(session, csv_prueba, today=HOY)
    session.commit()

    assert report.with_odds == 2
    pelea = pelea_por_id(session, "7db07db07db07db1")
    odds = session.scalar(select(Odds).where(Odds.fight_id == pelea.id))
    # Allen (-150) es B y Duncan (+130) es A
    assert (odds.price_a, odds.price_b) == (2.3, 1.667)
    assert odds.bookmaker == "kaggle"

    pelea = pelea_por_id(session, "295f295f295f2951")
    odds = session.scalar(select(Odds).where(Odds.fight_id == pelea.id))
    assert (odds.price_a, odds.price_b) == (1.8, 2.05)


def test_importar_dos_veces_no_duplica(session, csv_prueba):
    import_csv(session, csv_prueba, today=HOY)
    session.commit()
    report = import_csv(session, csv_prueba, today=HOY)
    session.commit()

    assert (report.events_new, report.fighters_new, report.fights_new) == (0, 0, 0)
    assert report.fights_updated == 10
    assert contar(session, Fight) == 10
    assert contar(session, FightResult) == 8
    assert contar(session, FightStats) == 16
    assert contar(session, Odds) == 2


def test_resumen_impreso(session, csv_prueba, capsys):
    print_report(import_csv(session, csv_prueba, today=HOY))
    out = capsys.readouterr().out
    assert "Filas leídas: 12" in out
    assert "Submission -> SUB" in out
    assert "1  ganador no reconocido" in out
    assert "winner = 'Mike Thirteen'" in out


def test_csv_en_latin1(session, tmp_path):
    fila = pelea("e0e0e0e0e0e0e0e1", ALLEN_DUNCAN,
                 peleador(1, "José Aldo", "e1e1e1e1e1e1e1e1"),
                 peleador(2, "Hotel Eight", "e2e2e2e2e2e2e2e2"))
    path = escribir_csv(tmp_path / "latin1.csv", [fila], encoding="latin-1")
    import_csv(session, path, today=HOY)
    session.commit()

    aldo = peleador_por_id(session, "e1e1e1e1e1e1e1e1")
    assert aldo.name == "José Aldo"
    assert pelea_por_id(session, "e0e0e0e0e0e0e0e1").result.winner_id == aldo.id


def test_csv_de_otro_dataset(session, tmp_path):
    path = tmp_path / "otro.csv"
    path.write_text("fighter_1,fighter_2\nAlpha,Bravo\n", encoding="utf-8")
    with pytest.raises(WrongCsvError, match="fight_url"):
        import_csv(session, path, today=HOY)


# ---------------------------------------------------------------------------
# Funciones sueltas
# ---------------------------------------------------------------------------

def test_cuotas_americanas_y_decimales():
    assert decimal_odds("-150") == 1.667
    assert decimal_odds("+130") == 2.3
    assert decimal_odds("100") == 2.0
    assert decimal_odds("1.80") == 1.8
    assert decimal_odds("") is None
    assert decimal_odds("0.5") is None  # no es una cuota


def test_metodos():
    assert normalize_result("KO/TKO", "Punches") == ("KO/TKO", "Punches")
    assert normalize_result("TKO - Doctor's Stoppage", "") == ("KO/TKO", "Doctor's Stoppage")
    assert normalize_result("Submission", "Rear Naked Choke") == ("SUB", "Rear Naked Choke")
    assert normalize_result("Decision - Majority", "Jueces...") == ("DEC", "Majority")
    assert normalize_result("DQ", "Illegal Knee") == ("DQ", "Illegal Knee")
    assert normalize_result("Could Not Continue", "") == ("OTHER", "Could Not Continue")


def test_ganador_sin_tildes_ni_mayusculas():
    def p(name):
        return CsvFighter(ufcstats_id="x", name=name, full_name=name, nickname=None,
                          height_cm=None, reach_cm=None, stance=None, birth_date=None,
                          wins=0, losses=0, draws=0, no_contests=0)

    pareja = (p("José Aldo"), p("Max Holloway"))
    assert winner_index("JOSE ALDO", pareja) == 0
    assert winner_index("Max Holloway", pareja) == 1
    assert winner_index("", pareja) is None
    assert winner_index("Draw", pareja) is None
    with pytest.raises(ValueError):
        winner_index("Conor McGregor", pareja)
