from sqlalchemy import create_engine, Column, String, Float, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

engine = create_engine("sqlite:///app.db")
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
