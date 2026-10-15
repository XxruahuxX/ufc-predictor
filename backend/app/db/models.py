"""Esquema de la base de datos (SQLAlchemy 2.0).

Convención importante: en cada pelea hay un "peleador A" (rojo) y un
"peleador B" (azul). Todas las probabilidades se guardan como
P(gana A). Así nunca hay dudas de a quién se refiere un número.

Tablas:
    events                 eventos (UFC 310, Fight Night...)
    fighters               peleadores
    fighter_aliases        otros nombres de un peleador en otras fuentes
    fights                 peleas de un evento
    fight_results          resultado real de una pelea
    fight_stats            estadísticas de cada peleador en una pelea
    sources                fuentes de predicciones (webs, Reddit, tipsters...)
    external_predictions   predicciones de las fuentes
    source_scores          historial de precisión y peso de cada fuente
    odds                   cuotas de casas de apuestas
    model_versions         versiones entrenadas del modelo propio
    app_predictions        predicciones finales de la app, con desglose
"""
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Eventos y peleadores
# ---------------------------------------------------------------------------

class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Identificador en UFCStats. Sirve para no duplicar al reimportar.
    ufcstats_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    date: Mapped[date] = mapped_column(Date, index=True)
    location: Mapped[str | None] = mapped_column(String(200))
    # "upcoming" (próximo) o "completed" (ya celebrado)
    status: Mapped[str] = mapped_column(String(20), default="upcoming")

    fights: Mapped[list["Fight"]] = relationship(
        back_populates="event", order_by="Fight.bout_order"
    )

    __table_args__ = (
        CheckConstraint("status IN ('upcoming', 'completed')", name="ck_event_status"),
    )


class Fighter(Base):
    __tablename__ = "fighters"

    id: Mapped[int] = mapped_column(primary_key=True)
    ufcstats_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    nickname: Mapped[str | None] = mapped_column(String(120))
    # Guardamos fecha de nacimiento, no edad: la edad depende de la fecha
    # de cada pelea y la necesitamos así para el backtesting.
    birth_date: Mapped[date | None] = mapped_column(Date)
    height_cm: Mapped[float | None] = mapped_column(Float)
    reach_cm: Mapped[float | None] = mapped_column(Float)
    stance: Mapped[str | None] = mapped_column(String(20))  # Orthodox, Southpaw, Switch
    weight_class: Mapped[str | None] = mapped_column(String(40))
    # Récord total (incluye peleas fuera de UFC). Racha y últimas peleas
    # se calculan a partir de la tabla fights, no se guardan aquí.
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    draws: Mapped[int] = mapped_column(Integer, default=0)
    no_contests: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    aliases: Mapped[list["FighterAlias"]] = relationship(back_populates="fighter")


class FighterAlias(Base):
    """Nombre de un peleador tal como aparece en otra fuente.

    Ejemplo: en Reddit pueden escribir "Poatan" para Alex Pereira.
    """

    __tablename__ = "fighter_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    fighter_id: Mapped[int] = mapped_column(ForeignKey("fighters.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(120), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))

    fighter: Mapped[Fighter] = relationship(back_populates="aliases")

    __table_args__ = (UniqueConstraint("alias", "source_id", name="uq_alias_source"),)


# ---------------------------------------------------------------------------
# Peleas y resultados
# ---------------------------------------------------------------------------

class Fight(Base):
    __tablename__ = "fights"

    id: Mapped[int] = mapped_column(primary_key=True)
    ufcstats_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    fighter_a_id: Mapped[int] = mapped_column(ForeignKey("fighters.id"), index=True)
    fighter_b_id: Mapped[int] = mapped_column(ForeignKey("fighters.id"), index=True)
    weight_class: Mapped[str | None] = mapped_column(String(40))
    is_title_fight: Mapped[bool] = mapped_column(Boolean, default=False)
    scheduled_rounds: Mapped[int] = mapped_column(Integer, default=3)
    # Posición en la cartelera: 1 = pelea estelar
    bout_order: Mapped[int | None] = mapped_column(Integer)
    # "scheduled", "completed" o "cancelled"
    status: Mapped[str] = mapped_column(String(20), default="scheduled")

    event: Mapped[Event] = relationship(back_populates="fights")
    fighter_a: Mapped[Fighter] = relationship(foreign_keys=[fighter_a_id])
    fighter_b: Mapped[Fighter] = relationship(foreign_keys=[fighter_b_id])
    result: Mapped["FightResult | None"] = relationship(back_populates="fight", uselist=False)
    stats: Mapped[list["FightStats"]] = relationship(back_populates="fight")

    __table_args__ = (
        CheckConstraint("fighter_a_id <> fighter_b_id", name="ck_fight_distinct_fighters"),
        CheckConstraint("scheduled_rounds IN (3, 5)", name="ck_fight_rounds"),
        CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled')", name="ck_fight_status"
        ),
    )


class FightResult(Base):
    """Resultado real. Una fila por pelea celebrada."""

    __tablename__ = "fight_results"

    fight_id: Mapped[int] = mapped_column(
        ForeignKey("fights.id", ondelete="CASCADE"), primary_key=True
    )
    # Vacío si es empate o sin resultado (no contest)
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("fighters.id"))
    # "win", "draw" o "no_contest"
    outcome: Mapped[str] = mapped_column(String(20))
    # "KO/TKO", "SUB", "DEC" (decisión), "DQ" u "OTHER"
    method: Mapped[str] = mapped_column(String(20))
    method_detail: Mapped[str | None] = mapped_column(String(120))  # "Rear Naked Choke", "Unanimous"
    end_round: Mapped[int] = mapped_column(Integer)
    end_time_seconds: Mapped[int] = mapped_column(Integer)  # dentro del asalto

    fight: Mapped[Fight] = relationship(back_populates="result")

    __table_args__ = (
        CheckConstraint("outcome IN ('win', 'draw', 'no_contest')", name="ck_result_outcome"),
        CheckConstraint(
            "method IN ('KO/TKO', 'SUB', 'DEC', 'DQ', 'OTHER')", name="ck_result_method"
        ),
        # Si hay victoria tiene que haber ganador, y al revés
        CheckConstraint(
            "(outcome = 'win') = (winner_id IS NOT NULL)", name="ck_result_winner"
        ),
    )


class FightStats(Base):
    """Estadísticas de un peleador en una pelea concreta (de UFCStats).

    Guardamos totales, no medias. Las medias por minuto (golpes por minuto,
    derribos cada 15 min...) se calculan sumando peleas anteriores a una
    fecha. Así el modelo nunca ve datos del futuro.
    """

    __tablename__ = "fight_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id", ondelete="CASCADE"), index=True)
    fighter_id: Mapped[int] = mapped_column(ForeignKey("fighters.id"), index=True)
    knockdowns: Mapped[int] = mapped_column(Integer, default=0)
    sig_strikes_landed: Mapped[int] = mapped_column(Integer, default=0)
    sig_strikes_attempted: Mapped[int] = mapped_column(Integer, default=0)
    total_strikes_landed: Mapped[int] = mapped_column(Integer, default=0)
    total_strikes_attempted: Mapped[int] = mapped_column(Integer, default=0)
    takedowns_landed: Mapped[int] = mapped_column(Integer, default=0)
    takedowns_attempted: Mapped[int] = mapped_column(Integer, default=0)
    submission_attempts: Mapped[int] = mapped_column(Integer, default=0)
    control_seconds: Mapped[int] = mapped_column(Integer, default=0)
    # Duración total de la pelea en segundos (para calcular "por minuto")
    fight_seconds: Mapped[int] = mapped_column(Integer)

    fight: Mapped[Fight] = relationship(back_populates="stats")

    __table_args__ = (UniqueConstraint("fight_id", "fighter_id", name="uq_stats_fight_fighter"),)


# ---------------------------------------------------------------------------
# Fuentes externas y sus predicciones
# ---------------------------------------------------------------------------

class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # "website", "reddit", "tipster", "forum" o "media"
    kind: Mapped[str] = mapped_column(String(20))
    url: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Confianza a usar si la fuente no da una (0.5 < x <= 1)
    default_confidence: Mapped[float] = mapped_column(Float, default=0.6)

    predictions: Mapped[list["ExternalPrediction"]] = relationship(back_populates="source")

    __table_args__ = (
        CheckConstraint(
            "kind IN ('website', 'reddit', 'tipster', 'forum', 'media')", name="ck_source_kind"
        ),
    )


class ExternalPrediction(Base):
    """Predicción de una fuente sobre una pelea.

    Los campos de evaluación (is_correct, brier) se rellenan cuando
    se conoce el resultado.
    """

    __tablename__ = "external_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id", ondelete="CASCADE"), index=True)
    predicted_winner_id: Mapped[int] = mapped_column(ForeignKey("fighters.id"))
    # Probabilidad que la fuente da a SU ganador (entre 0.5 y 1)
    confidence: Mapped[float] = mapped_column(Float)
    predicted_method: Mapped[str | None] = mapped_column(String(20))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    url: Mapped[str | None] = mapped_column(String(500))  # de dónde sale, para comprobarlo

    # Evaluación (vacío hasta que hay resultado)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    brier: Mapped[float | None] = mapped_column(Float)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[Source] = relationship(back_populates="predictions")
    fight: Mapped[Fight] = relationship()

    __table_args__ = (
        # Una predicción por fuente y pelea. Si la fuente cambia de opinión,
        # se actualiza la fila (nos quedamos con la última antes del evento).
        UniqueConstraint("source_id", "fight_id", name="uq_prediction_source_fight"),
        CheckConstraint("confidence >= 0.5 AND confidence <= 1", name="ck_prediction_confidence"),
        CheckConstraint("brier IS NULL OR (brier >= 0 AND brier <= 1)", name="ck_prediction_brier"),
    )


class SourceScore(Base):
    """Foto de la precisión de una fuente en un momento dado.

    Se añade una fila nueva tras cada evento. Así se puede ver cómo
    evoluciona el peso de cada fuente con el tiempo.
    """

    __tablename__ = "source_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    n_evaluated: Mapped[int] = mapped_column(Integer)
    accuracy: Mapped[float] = mapped_column(Float)  # % de aciertos, de 0 a 1
    brier: Mapped[float] = mapped_column(Float)  # media; menor es mejor
    weight: Mapped[float] = mapped_column(Float)  # 0 si no llega al mínimo de predicciones


# ---------------------------------------------------------------------------
# Cuotas
# ---------------------------------------------------------------------------

class Odds(Base):
    """Cuotas de una casa de apuestas en un momento dado (formato decimal)."""

    __tablename__ = "odds"

    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id", ondelete="CASCADE"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(80))
    price_a: Mapped[float] = mapped_column(Float)  # p. ej. 1.45
    price_b: Mapped[float] = mapped_column(Float)  # p. ej. 2.80
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("price_a > 1 AND price_b > 1", name="ck_odds_prices"),
        UniqueConstraint("fight_id", "bookmaker", "captured_at", name="uq_odds_snapshot"),
    )


# ---------------------------------------------------------------------------
# Modelo propio y predicciones de la app
# ---------------------------------------------------------------------------

class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(40), unique=True)  # "2026-10-15-logreg"
    algorithm: Mapped[str] = mapped_column(String(40))  # "logistic_regression", "gradient_boosting"
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # accuracy, brier, log_loss
    file_path: Mapped[str] = mapped_column(String(300))  # archivo .joblib
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class AppPrediction(Base):
    """Predicción final de la app para una pelea, con su desglose.

    Se recalcula cada vez que llegan datos nuevos. La fila con
    is_final = True es la que se congela antes del evento y la que
    cuenta para el histórico de aciertos.
    """

    __tablename__ = "app_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id", ondelete="CASCADE"), index=True)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Componentes, todos como P(gana A). Vacío si no hay datos.
    prob_a_model: Mapped[float | None] = mapped_column(Float)
    prob_a_sources: Mapped[float | None] = mapped_column(Float)
    prob_a_market: Mapped[float | None] = mapped_column(Float)
    # Pesos usados para combinar: {"model": 0.5, "sources": 0.2, "market": 0.3}
    weights: Mapped[dict] = mapped_column(JSON, default=dict)

    # Resultado de la combinación
    prob_a_final: Mapped[float] = mapped_column(Float)
    # Intervalo de incertidumbre de prob_a_final
    prob_a_low: Mapped[float] = mapped_column(Float)
    prob_a_high: Mapped[float] = mapped_column(Float)
    # Probabilidad de cada método: {"KO/TKO": 0.4, "SUB": 0.1, "DEC": 0.5}
    method_probs: Mapped[dict] = mapped_column(JSON, default=dict)

    is_final: Mapped[bool] = mapped_column(Boolean, default=False)

    # Evaluación (vacío hasta que hay resultado)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    brier: Mapped[float | None] = mapped_column(Float)

    fight: Mapped[Fight] = relationship()

    __table_args__ = (
        CheckConstraint("prob_a_final >= 0 AND prob_a_final <= 1", name="ck_app_prob"),
        CheckConstraint(
            "prob_a_low <= prob_a_final AND prob_a_final <= prob_a_high", name="ck_app_interval"
        ),
    )
