"""
SQLAlchemy ORM Models for the Elexon Wind Procurement PostgreSQL Schema.
Stores raw Elexon WINDFOR forecast revisions (`start_time_utc`, `publish_time_utc`,
`forecast_mw`) and FUELHH half-hourly/hourly wind settlement actuals.
"""

try:
    from sqlalchemy import Column, Float, Index, Integer, String, UniqueConstraint
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

    class RawWindforForecast(Base):
        __tablename__ = "raw_windfor_forecasts"

        id = Column(Integer, primary_key=True, autoincrement=True)
        start_time_utc = Column(String(20), nullable=False)
        publish_time_utc = Column(String(20), nullable=False)
        delivery_date = Column(String(10), nullable=False)
        target_hour_utc = Column(String(5), nullable=False)
        forecast_mw = Column(Float, nullable=False)

        __table_args__ = (
            UniqueConstraint("start_time_utc", "publish_time_utc", name="uq_windfor_start_pub"),
            Index("idx_windfor_causal_lookup", "delivery_date", "publish_time_utc", "target_hour_utc"),
            Index("idx_windfor_start_pub", "start_time_utc", "publish_time_utc"),
        )

    class RawFuelhhActual(Base):
        __tablename__ = "raw_fuelhh_actuals"

        start_time_utc = Column(String(20), primary_key=True)
        delivery_date = Column(String(10), nullable=False)
        month = Column(String(7), nullable=False)
        target_hour_utc = Column(String(5), nullable=False)
        split_set = Column(String(24), nullable=False)
        slot_00_mw = Column(Float, nullable=False)
        slot_30_mw = Column(Float, nullable=False)
        actual_wind_mw = Column(Float, nullable=False)
        rolling_7d_wind_mw = Column(Float, nullable=False)
        seasonal_regime_ratio = Column(Float, nullable=False)

        __table_args__ = (
            Index("idx_fuelhh_delivery_date", "delivery_date", "target_hour_utc"),
        )

    HAS_SQLALCHEMY = True

except ImportError:
    Base = None
    RawWindforForecast = None
    RawFuelhhActual = None
    HAS_SQLALCHEMY = False
