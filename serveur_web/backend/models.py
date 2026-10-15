from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, create_engine, event, text
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
    current_zone = Column(String, default="Zone_1")
    secret_share = Column(String, default="")  # Part secrète G1 au format JSON
    accumulated_pk = Column(String, default="")  # Clé publique combinée G2 au format JSON


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
    owner_pk = Column(String, default="")  # Clé publique G2 de la zone
    is_delegated = Column(Integer, default=0)
    delegate_id = Column(String, default="")
    delegate_pk = Column(String, default="")
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


def _migrate_db():
    """Ajoute de manière idempotente les colonnes manquantes dans SQLite."""
    with engine.connect() as conn:
        try:
            # Table stream_sessions
            res = conn.execute(text("PRAGMA table_info(stream_sessions)"))
            existing_cols = {row[1] for row in res.fetchall()}
            if "owner_pk" not in existing_cols:
                conn.execute(text("ALTER TABLE stream_sessions ADD COLUMN owner_pk VARCHAR DEFAULT ''"))
            if "is_delegated" not in existing_cols:
                conn.execute(text("ALTER TABLE stream_sessions ADD COLUMN is_delegated INTEGER DEFAULT 0"))
            if "delegate_id" not in existing_cols:
                conn.execute(text("ALTER TABLE stream_sessions ADD COLUMN delegate_id VARCHAR DEFAULT ''"))
            if "delegate_pk" not in existing_cols:
                conn.execute(text("ALTER TABLE stream_sessions ADD COLUMN delegate_pk VARCHAR DEFAULT ''"))

            # Table users
            res_users = conn.execute(text("PRAGMA table_info(users)"))
            existing_user_cols = {row[1] for row in res_users.fetchall()}
            if "current_zone" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN current_zone VARCHAR DEFAULT 'Zone_1'"))
            if "secret_share" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN secret_share VARCHAR DEFAULT ''"))
            if "accumulated_pk" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN accumulated_pk VARCHAR DEFAULT ''"))

            conn.commit()
        except Exception as e:
            print(f"[DB Migration] Avertissement migration: {e}", flush=True)


_migrate_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
