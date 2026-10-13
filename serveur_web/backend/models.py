from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
import os
import datetime

os.makedirs("./data", exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")

connect_args = {}
if "sqlite" in DATABASE_URL:
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=connect_args)

# Activation du mode WAL et du busy_timeout pour SQLite afin de fiabiliser les accès concurrents
if "sqlite" in DATABASE_URL:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="viewer")  # "viewer", "streamer", "admin"


class EdgeNode(Base):
    __tablename__ = "edge_nodes"
    
    node_id = Column(String, primary_key=True, index=True)
    zone_id = Column(String, index=True)
    ip_address = Column(String)
    wss_port = Column(Integer)
    storage_available_gb = Column(Float)
    cpu_usage_pct = Column(Float)
    last_seen = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class StreamSession(Base):
    __tablename__ = "stream_sessions"

    session_id = Column(String, primary_key=True, index=True)
    owner_email = Column(String, index=True)
    node_id = Column(String, ForeignKey("edge_nodes.node_id"), index=True)
    zone_id = Column(String)
    status = Column(String, default="live", index=True)  # "live", "stopped", "failed"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_active = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class DataLocation(Base):
    __tablename__ = "data_locations"
    
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String, index=True)
    node_id = Column(String, ForeignKey("edge_nodes.node_id"), index=True)
    blocks_count = Column(Integer, default=0)
    total_size_kb = Column(Float, default=0.0)


# Création idempotente des tables dans la base
Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
