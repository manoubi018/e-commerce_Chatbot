from app.database import Base
from sqlalchemy import Column, String, Float, JSON


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"

    id = Column(String, primary_key=True)
    provider = Column(String)
    model = Column(String)
    time_sec = Column(Float)
    cost_usd = Column(Float)
    data = Column(JSON)
