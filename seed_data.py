import os
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
DB_URL = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)
engine = create_engine(DB_URL, pool_pre_ping=True)

today = date.today()

items = [
    # -------- veg / fruit --------
    ("tomato",           6,   "pcs", "veg",       "veg",     today + timedelta(days=1)),
    ("onion",            4,   "pcs", "veg",       "veg",     today + timedelta(days=6)),
    ("potato",           8,   "pcs", "veg",       "veg",     today + timedelta(days=10)),
    ("spinach",          1,   "bunch","veg",      "veg",     today + timedelta(days=2)),
    ("capsicum",         2,   "pcs", "veg",       "veg",     today + timedelta(days=4)),
    ("carrot",           5,   "pcs", "veg",       "veg",     today + timedelta(days=5)),
    ("broccoli",         1,   "head","veg",       "veg",     today + timedelta(days=3)),
    ("cucumber",         2,   "pcs", "veg",       "veg",     today + timedelta(days=3)),
    ("mushroom",         200, "g",   "veg",       "veg",     today + timedelta(days=2)),
    ("ginger",           80,  "g",   "veg",       "veg",     today + timedelta(days=12)),
    ("garlic",           1,   "bulb","veg",       "veg",     today + timedelta(days=20)),
    ("banana",           6,   "pcs", "fruit",     "veg",     today + timedelta(days=2)),
    ("apple",            4,   "pcs", "fruit",     "veg",     today + timedelta(days=7)),
    ("mango pulp",       1,   "can", "fruit",     "veg",     today + timedelta(days=90)),
    # -------- grain / staples --------
    ("rice",             2,   "kg",  "grain",     "veg",     today + timedelta(days=180)),
    ("atta flour",       1,   "kg",  "grain",     "veg",     today + timedelta(days=120)),
    ("pasta",            500, "g",   "grain",     "veg",     today + timedelta(days=365)),
    ("bread",            6,   "slices","grain",   "veg",     today + timedelta(days=1)),
    ("noodles",          300, "g",   "grain",     "veg",     today + timedelta(days=200)),
    # -------- dairy --------
    ("milk",             1,   "L",   "dairy",     "veg",     today + timedelta(days=2)),
    ("paneer",           250, "g",   "dairy",     "veg",     today + timedelta(days=3)),
    ("yogurt",           400, "g",   "dairy",     "veg",     today + timedelta(days=5)),
    ("cheese",           200, "g",   "dairy",     "veg",     today + timedelta(days=14)),
    ("butter",           200, "g",   "dairy",     "veg",     today + timedelta(days=30)),
    # -------- condiments / spices --------
    ("salt",             1,   "kg",  "condiment", "veg",     today + timedelta(days=3650)),
    ("sugar",            1,   "kg",  "condiment", "veg",     today + timedelta(days=3650)),
    ("turmeric",         100, "g",   "condiment", "veg",     today + timedelta(days=365)),
    ("cumin seeds",      100, "g",   "condiment", "veg",     today + timedelta(days=365)),
    ("garam masala",     50,  "g",   "condiment", "veg",     today + timedelta(days=365)),
    ("soy sauce",        200, "ml",  "condiment", "veg",     today + timedelta(days=365)),
    ("tomato ketchup",   350, "g",   "condiment", "veg",     today + timedelta(days=365)),
    # -------- protein (non-veg) --------
    ("chicken breast",   750, "g",   "protein",   "non-veg", today + timedelta(days=2)),
    ("fish fillet",      500, "g",   "protein",   "non-veg", today + timedelta(days=1)),
    ("prawns",           300, "g",   "protein",   "non-veg", today + timedelta(days=2)),
    ("eggs",             12,  "pcs", "protein",   "eggs-ok", today + timedelta(days=10)),
    ("mutton",           600, "g",   "protein",   "non-veg", today + timedelta(days=2)),
    ("bacon",            200, "g",   "protein",   "non-veg", today + timedelta(days=7)),
    ("tuna (canned)",    1,   "can", "protein",   "non-veg", today + timedelta(days=365)),
    # -------- veg proteins --------
    ("tofu",             300, "g",   "protein",   "vegan",   today + timedelta(days=4)),
    ("chickpeas (canned)",1, "can",  "protein",   "vegan",   today + timedelta(days=365)),
    ("rajma (kidney beans)",500,"g", "protein",   "vegan",   today + timedelta(days=300)),
]

with engine.begin() as conn:
    for name, qty, unit, category, diet_type, exp in items:
        conn.execute(text("""
            INSERT INTO ingredients (name, qty, unit, category, diet_type, expires_on)
            VALUES (:name, :qty, :unit, :category, :diet_type, :expires_on)
            ON DUPLICATE KEY UPDATE
              qty=VALUES(qty), unit=VALUES(unit),
              category=VALUES(category), diet_type=VALUES(diet_type),
              expires_on=VALUES(expires_on)
        """), {
            "name": name, "qty": qty, "unit": unit,
            "category": category, "diet_type": diet_type,
            "expires_on": exp.isoformat()
        })

print("Seeded sample data ✔")