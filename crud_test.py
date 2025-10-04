import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
url = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)
engine = create_engine(url, pool_pre_ping=True)

with engine.begin() as conn:
    conn.execute(text("""
        INSERT INTO ingredients (name, qty, unit, category, expires_on)
        VALUES (:name, :qty, :unit, :category, :expires_on)
        ON DUPLICATE KEY UPDATE
          qty=VALUES(qty), unit=VALUES(unit), category=VALUES(category), expires_on=VALUES(expires_on)
    """), {"name":"tomato","qty":3,"unit":"pcs","category":"veg","expires_on":"2025-10-06"})

    rows = conn.execute(text("""
        SELECT id, name, qty, unit, category, COALESCE(DATE_FORMAT(expires_on, '%Y-%m-%d'), NULL) AS expires_on
        FROM ingredients ORDER BY id DESC LIMIT 5
    """)).mappings().all()

print("Latest rows:")
for r in rows:
    print(dict(r))