import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
DB_HOST=os.getenv("DB_HOST"); DB_PORT=os.getenv("DB_PORT")
DB_USER=os.getenv("DB_USER"); DB_PASS=os.getenv("DB_PASS"); DB_NAME=os.getenv("DB_NAME")

url=f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
print("Connecting to:", url)

engine=create_engine(url, pool_pre_ping=True, pool_recycle=180, connect_args={"connect_timeout": 5})
with engine.begin() as conn:
    print("MySQL version:", conn.execute(text("SELECT VERSION();")).scalar())