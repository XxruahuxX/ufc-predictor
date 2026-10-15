"""Evalúa las predicciones externas y muestra el ranking de fuentes.

Uso desde backend/:
    python -m app.scoring                    # hasta hoy
    python -m app.scoring --hasta 2025-12-31 # como se veía ese día

Pasos:
    1. Acierto y Brier de cada predicción con resultado (evaluate.py).
    2. Acierto, Brier y peso de cada fuente (sources.py).
    3. Guarda una foto en source_scores si hay predicciones evaluadas nuevas.
"""
import argparse
from datetime import date

from sqlalchemy import select

from app.config import settings
from app.db.models import Source
from app.scoring.evaluate import evaluate_predictions
from app.scoring.sources import Score, market_score, save_scores, score_sources


def print_ranking(scores: list[Score], market: Score | None, without: list[str], as_of: date) -> None:
    print(f"\nRanking de fuentes hasta el {as_of}")
    print(f"Peso 0 = menos de {settings.source_min_predictions} predicciones evaluadas "
          "o no mejora a decir siempre 50 %.")
    print(f"  {'Fuente':<28} {'Evaluadas':>9} {'Acierto':>8} {'Brier':>6} {'Peso':>5}")
    for s in scores:
        print(f"  {s.name[:28]:<28} {s.n:>9} {s.accuracy:>7.1%} {s.brier:>6.3f} {s.weight:>5.2f}")
    if not scores:
        print("  (ninguna fuente tiene predicciones evaluadas todavía)")
    if market is not None:
        print(f"  {market.name:<28} {market.n:>9} {market.accuracy:>7.1%} {market.brier:>6.3f}"
              f" {'—':>5}  referencia")
    if without:
        print(f"Fuentes sin predicciones evaluadas: {', '.join(without)}")


def main() -> None:
    from app.db.session import SessionLocal  # aquí, para que los tests no se conecten

    parser = argparse.ArgumentParser(description="Evalúa las predicciones y puntúa las fuentes")
    parser.add_argument("--hasta", type=date.fromisoformat, default=date.today(),
                        help="fecha AAAA-MM-DD; solo cuentan los eventos hasta ese día")
    args = parser.parse_args()

    with SessionLocal() as session:
        report = evaluate_predictions(session)
        print(f"Predicciones evaluadas: {report.evaluated}. "
              f"Sin valor (cancelada, empate o cambio de rival): {report.void}. "
              f"Pendientes de resultado: {report.pending}.")
        scores = score_sources(session, args.hasta)
        market = market_score(session, args.hasta)
        scored = {s.name for s in scores}
        without = [name for name in session.scalars(select(Source.name).order_by(Source.name))
                   if name not in scored]
        print_ranking(scores, market, without, args.hasta)
        if args.hasta == date.today():  # una foto del pasado no se guarda
            saved = save_scores(session, scores)
            if saved:
                print(f"Guardada la puntuación de {saved} fuentes en source_scores.")
        session.commit()


if __name__ == "__main__":
    main()
