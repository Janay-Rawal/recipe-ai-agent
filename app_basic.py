import os
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from datetime import date

load_dotenv()
url = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)
engine = create_engine(url, pool_pre_ping=True)

st.set_page_config(page_title="Pantry DB – Basic", page_icon="🥫", layout="wide")
st.title("🥫 Pantry (MySQL) — Basic")

# List ingredients
with engine.begin() as conn:
    rows = conn.execute(text("""
        SELECT id, name, qty, unit, category,
               COALESCE(DATE_FORMAT(expires_on, '%Y-%m-%d'), NULL) AS expires_on,
               created_at, updated_at
        FROM ingredients ORDER BY name ASC
    """)).mappings().all()

st.subheader("Current ingredients")
if rows:
    st.dataframe([dict(r) for r in rows], width="stretch")
else:
    st.info("No ingredients yet.")

# Add / update form (upsert by name)
st.subheader("Add / Update ingredient")
with st.form("add_form"):
    name = st.text_input("Name *").strip().lower()
    qty = st.number_input("Quantity", min_value=0.0, value=1.0, step=0.5)
    unit = st.text_input("Unit", value="pcs")
    category = st.selectbox("Category", ["veg","fruit","grain","dairy","protein","condiment","other"], index=0)
    expires = st.date_input("Expires on (optional)", value=date.today())
    submitted = st.form_submit_button("Save")
    if submitted:
        if not name:
            st.error("Name is required.")
        else:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO ingredients (name, qty, unit, category, expires_on)
                    VALUES (:name, :qty, :unit, :category, :expires_on)
                    ON DUPLICATE KEY UPDATE
                      qty=VALUES(qty), unit=VALUES(unit),
                      category=VALUES(category), expires_on=VALUES(expires_on)
                """), {
                    "name": name, "qty": float(qty), "unit": unit, "category": category,
                    "expires_on": expires.isoformat() if expires else None
                })
            st.success(f"Saved: {name}")
            st.rerun()