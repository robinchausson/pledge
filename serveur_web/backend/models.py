from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime

SQLALCHEMY_DATABASE_URL = "sqlite:///./app.db" # Base de données locale pour le développement
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="viewer") # ex: "viewer", "streamer", "crypto_node"

class EdgeNode(Base):
    __tablename__ = "edge_nodes"
    
    node_id = Column(String, primary_key=True, index=True)
    zone_id = Column(String, index=True)
    ip_address = Column(String)
    wss_port = Column(Integer)
    storage_available_gb = Column(Float)
    cpu_usage_pct = Column(Float)
    last_seen = Column(DateTime, default=datetime.datetime.utcnow)

class DataLocation(Base):
    __tablename__ = "data_locations"
    
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String, index=True)
    node_id = Column(String, ForeignKey("edge_nodes.node_id"))
    blocks_count = Column(Integer)
    total_size_kb = Column(Float)

# Créer les tables dans la DB
Base.metadata.create_all(bind=engine)

# Dépendance pour obtenir la session de base de données
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
