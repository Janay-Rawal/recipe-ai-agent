# app_recipes.py
import os
import re
import json
from datetime import date, datetime
from typing import List, Dict, Any, Optional

import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from langchain.prompts import ChatPromptTemplate
from langchain_community.llms import Ollama
from langchain.schema import StrOutputParser


# -------------------- Config --------------------
load_dotenv()
DB_URL = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)

# Change if your Ollama server isn’t default:
OLLAMA_BASE_URL: Optional[str] = None  # e.g., "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.1:latest"

st.set_page_config(page_title="🍳 AI Recipe Agent", page_icon="🍳", layout="wide")
st.title("🍳 AI Recipe Agent — MySQL + LangChain + Ollama")

# -------------------- DB Helpers --------------------
@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    return create_engine(DB_URL, pool_pre_ping=True, pool_recycle=180)

def ensure_tables(engine: Engine):
    DDL_INGREDIENTS = """
    CREATE TABLE IF NOT EXISTS ingredients (
      id INT AUTO_INCREMENT PRIMARY KEY,
      name VARCHAR(191) NOT NULL,
      qty DOUBLE DEFAULT 0,
      unit VARCHAR(64) DEFAULT '',
      category VARCHAR(64) DEFAULT '',
      diet_type VARCHAR(16) NOT NULL DEFAULT 'unknown',
      expires_on DATE NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY unique_name (name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    DDL_HISTORY = """
    CREATE TABLE IF NOT EXISTS recipe_history (
      id INT AUTO_INCREMENT PRIMARY KEY,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      dietary VARCHAR(64),
      time_limit INT,
      servings INT,
      cuisine VARCHAR(128),
      num_options INT,
      ranked_snapshot MEDIUMTEXT,
      result_markdown LONGTEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with engine.begin() as conn:
        conn.execute(text(DDL_INGREDIENTS))
        conn.execute(text(DDL_HISTORY))

def list_ingredients(engine: Engine) -> List[Dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT id, name, qty, unit, category, diet_type,
                   DATE_FORMAT(expires_on, '%Y-%m-%d') AS expires_on,
                   created_at, updated_at
            FROM ingredients
            ORDER BY name ASC
        """)).mappings().all()
        return [dict(r) for r in rows]

def upsert_ingredient(
    engine,
    name: str,
    qty: float,
    unit: str,
    category: str,
    diet_type: str = "unknown",           # <- default added
    expires_on: str | None = None,
):
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ingredients (name, qty, unit, category, diet_type, expires_on)
            VALUES (:name, :qty, :unit, :category, :diet_type, :expires_on)
            ON DUPLICATE KEY UPDATE
              qty = VALUES(qty),
              unit = VALUES(unit),
              category = VALUES(category),
              diet_type = VALUES(diet_type),
              expires_on = VALUES(expires_on)
        """), {
            "name": name.strip().lower(),
            "qty": qty,
            "unit": unit,
            "category": category,
            "diet_type": diet_type,
            "expires_on": expires_on
        })
def delete_ingredient(engine: Engine, ingredient_id: int):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ingredients WHERE id = :id"), {"id": ingredient_id})

def list_history(engine: Engine, limit: int = 10):
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT id, created_at, dietary, time_limit, servings, cuisine, num_options
            FROM recipe_history ORDER BY id DESC LIMIT :lim
        """), {"lim": limit}).all()
        return rows

def get_history(engine: Engine, hist_id: int):
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT id, created_at, dietary, time_limit, servings, cuisine, num_options, ranked_snapshot, result_markdown
            FROM recipe_history WHERE id = :id
        """), {"id": hist_id}).one_or_none()
        return row

def save_history(engine: Engine, params: Dict[str, Any], snapshot: str, markdown: str):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO recipe_history (dietary, time_limit, servings, cuisine, num_options, ranked_snapshot, result_markdown)
            VALUES (:dietary, :time_limit, :servings, :cuisine, :num_options, :snap, :md)
        """), {
            "dietary": params["dietary"],
            "time_limit": params["time_limit"],
            "servings": params["servings"],
            "cuisine": params["cuisine"],
            "num_options": params["num_options"],
            "snap": snapshot,
            "md": markdown
        })

def apply_usage(engine: Engine, usage_items: list[dict]) -> dict:
    """
    Decrement quantities for each {"name","qty","unit"} in usage_items.
    If unit differs from pantry unit, we apply qty only (best-effort).
    Returns a result summary.
    """
    updated, missing = [], []
    with engine.begin() as conn:
        for it in usage_items:
            name = (it.get("name") or "").strip().lower()
            qty  = float(it.get("qty") or 0)
            if not name or qty <= 0:
                continue

            row = conn.execute(text(
                "SELECT id, qty, unit FROM ingredients WHERE name = :name"
            ), {"name": name}).mappings().one_or_none()

            if not row:
                missing.append(name)
                continue

            new_qty = max(0.0, float(row["qty"]) - qty)
            conn.execute(text(
                "UPDATE ingredients SET qty = :q WHERE id = :id"
            ), {"q": new_qty, "id": row["id"]})
            updated.append({"name": name, "old_qty": float(row["qty"]), "new_qty": new_qty})
    return {"updated": updated, "missing": missing}

_USAGE_BLOCK_RE = re.compile(
    r"```usage_json\s*(?P<json>[\s\S]*?)\s*```",
    re.IGNORECASE
)

def parse_usage_from_markdown(md: str) -> list[dict]:
    """
    Extracts and loads the fenced usage_json block.
    Returns: [{"title": "...", "items": [{"name": "...", "qty": 0, "unit": "..."}]}]
    """
    m = _USAGE_BLOCK_RE.search(md or "")
    if not m:
        return []
    try:
        return json.loads(m.group("json").strip())
    except Exception:
        return []

# -------------------- Ranking --------------------
def days_left(iso_date: Optional[str]) -> float:
    if not iso_date:
        return 9_999.0
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return 9_999.0
    return (dt - datetime.now()).total_seconds() / 86400.0

def rank_ingredients(
    items: List[Dict[str, Any]],
    selected_diet: str = "veg",
    exclude_non_veg: bool = True,
    exclude_eggs: bool = False,
    exclude_dairy: bool = False,
) -> List[Dict[str, Any]]:
    ranked = []
    for it in items:
        dleft = days_left(it.get("expires_on"))
        prio = 1.0 / max(dleft, 0.25)

        # perishables boost
        cat = (it.get("category") or "").lower()
        if cat in {"dairy", "protein", "veg", "vegetable", "fruit"}:
            prio *= 1.2

        # diet alignment boosts/penalties
        it_diet = (it.get("diet_type") or "unknown").lower()
        if exclude_non_veg and it_diet == "non-veg":
            prio *= 0.15   # strong penalty
        if exclude_eggs and it_diet == "eggs-ok":
            prio *= 0.4
        if exclude_dairy and cat == "dairy":
            prio *= 0.4
        if selected_diet == "vegan" and cat == "dairy":
            prio *= 0.4

        ranked.append({**it, "_days_left": dleft, "_priority": prio})

    ranked.sort(key=lambda x: (-x["_priority"], x["name"]))
    return ranked
def snapshot_block(ranked: List[Dict[str, Any]], limit: int = 14) -> str:
    lines = []
    for i, it in enumerate(ranked[:limit], start=1):
        d = round(it["_days_left"], 1)
        lines.append(f"{i}. {it['name']} {it['qty']}{it['unit']} | exp ~ {d}d | prio={round(it['_priority'],2)}")
    return "\n".join(lines) if lines else "(empty)"

def guess_category(name: str) -> str:
    n = name.lower()
    # non-veg
    if any(k in n for k in ["chicken", "mutton", "goat", "lamb", "beef", "pork", "fish", "prawn", "shrimp", "seafood", "turkey", "bacon", "sausage"]):
        return "protein"   # keep schema consistent (non-veg under protein)
    # eggs
    if "egg" in n:
        return "protein"
    # dairy
    if any(k in n for k in ["milk", "paneer", "cheese", "yogurt", "curd", "butter", "ghee", "cream"]):
        return "dairy"
    # veg/fruit
    if any(k in n for k in ["tomato","onion","potato","carrot","spinach","capsicum","pepper","cucumber","cabbage","cauliflower","broccoli","okra","bhindi","brinjal","eggplant"]):
        return "veg"
    if any(k in n for k in ["banana","apple","mango","orange","grape","berries","strawberry","pineapple","pear","papaya"]):
        return "fruit"
    # grains/condiments
    if any(k in n for k in ["rice","flour","atta","wheat","maida","bread","pasta","noodle","quinoa","oats","poha","suji","semolina"]):
        return "grain"
    if any(k in n for k in ["salt","sugar","ketchup","sauce","vinegar","soy","mustard","pickle","masala","spice","chilli","chili","turmeric","cumin","coriander","garam"]):
        return "condiment"
    return "other"

def guess_diet_type(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ["chicken","mutton","goat","lamb","beef","pork","fish","prawn","shrimp","seafood","turkey","bacon","sausage","ham","salami","anchovy","tuna"]):
        return "non-veg"
    if "egg" in n:
        return "eggs-ok"
    # default assumption
    return "veg"

_QTY_UNIT_RE = re.compile(
    r"""^\s*
        (?P<name>.*?)
        (?:\s+(?P<qty>\d+(?:\.\d+)?)(?P<unit>[a-zA-Z]+|pcs|pc|g|kg|ml|l|tbsp|tsp|cups?))?
        \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

def parse_line_to_item(line: str, default_unit: str, default_days: int) -> Dict[str, Any]:
    """
    Accepts lines like:
      'chicken breast 500g'
      'eggs 6pcs'
      'paneer 200 g'
      'tomato'  (falls back to qty=1, default_unit)
    """
    line = line.strip()
    if not line:
        return {}
    m = _QTY_UNIT_RE.match(line)
    if not m:
        return {}

    name = (m.group("name") or "").strip().lower()
    qty = 1.0
    unit = default_unit
    if m.group("qty"):
        try:
            qty = float(m.group("qty"))
        except ValueError:
            qty = 1.0
    if m.group("unit"):
        unit = m.group("unit").lower().replace("cup", "cups")

    category = guess_category(name)
    diet_type = guess_diet_type(name)
    expires_on = (date.today()).toordinal() + default_days
    expires_on = date.fromordinal(expires_on).isoformat()
    return {
        "name": name,
        "qty": qty,
        "unit": unit,
        "category": category,
        "diet_type": diet_type,
        "expires_on": expires_on,
    }

# -------------------- LLM Prompt --------------------
SYSTEM_RECIPE = """You are a helpful recipe creator that:
- prioritizes soon-to-expire items,
- maximizes use of the provided pantry,
- defaults to Indian kitchens unless otherwise requested,
- returns 2–3 options with: title, why-it-uses-expiring-items, total time, difficulty,
  ingredients (quantities), step-by-step method, substitutions, and dietary notes.
- do not invent unavailable ingredients unless optional substitutes."""

USER_TEMPLATE = """Pantry (expiry-ranked):
{ranked}

User constraints:
- Dietary: {dietary}
- Time limit (minutes): {time_limit}
- Servings: {servings}
- Cuisine: {cuisine}
- Exclusions: {{
  "non_veg": {exclude_non_veg},
  "eggs": {exclude_eggs},
  "dairy": {exclude_dairy}
}}

Rules:
- STRICTLY avoid ingredients that violate the exclusions/dietary constraints.
- Prefer at least 2 of the top 4 expiring items when possible.
- Use mostly pantry items; mark any non-pantry as OPTIONAL.
- Return clean, readable markdown for each recipe (title, time, ingredients, steps).
- At the END, include a fenced code block with the language tag "usage_json" that contains JSON like:
  [
    {{
      "title": "Recipe Title",
      "items": [{{"name": "tomato", "qty": 2, "unit": "pcs"}}, ...]
    }},
    ...
  ]
- In the JSON, normalize "name" to EXACT pantry item names; omit items not from pantry.

Create {num_options} distinct recipes.
"""

def generate_with_llm(ranked_block: str, dietary: str, time_limit: int, servings: int,
                      cuisine: str, num_options: int,
                      exclude_non_veg: bool, exclude_eggs: bool, exclude_dairy: bool) -> str:
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_RECIPE),
        ("user", USER_TEMPLATE),
    ])
    kwargs = {"model": OLLAMA_MODEL}
    if OLLAMA_BASE_URL:
        kwargs["base_url"] = OLLAMA_BASE_URL
    llm = Ollama(**kwargs)
    chain = prompt | llm | StrOutputParser()
    user_vars = {
        "ranked": ranked_block,
        "dietary": dietary,
        "time_limit": time_limit,
        "servings": servings,
        "cuisine": cuisine,
        "num_options": num_options,
        "exclude_non_veg": str(exclude_non_veg).lower(),
        "exclude_eggs": str(exclude_eggs).lower(),
        "exclude_dairy": str(exclude_dairy).lower(),
    }
    return chain.invoke(user_vars)

# -------------------- UI --------------------
engine = get_engine()
ensure_tables(engine)

with st.sidebar:
    st.header("Preferences")
    dietary = st.selectbox("Dietary", ["none","veg","eggs-ok","vegan","non-veg"], index=1)
    time_limit = st.slider("Time limit (min)", 10, 120, 30, 5)
    servings = st.slider("Servings", 1, 8, 2)
    cuisine = st.text_input("Cuisine", value="Indian")
    num_options = st.slider("Recipe options", 1, 3, 2)

    st.markdown("### Filters")
    exclude_non_veg = st.checkbox("Exclude non-veg", value=(dietary in {"veg","vegan"}))
    exclude_eggs    = st.checkbox("Exclude eggs",    value=(dietary == "vegan"))
    exclude_dairy   = st.checkbox("Exclude dairy",   value=(dietary == "vegan"))

    st.markdown("---")
    st.subheader("History")
    hist_rows = list_history(engine, limit=10)
    if hist_rows:
        pick = st.selectbox(
            "View a previous run",
            ["(select)"] + [f"#{hid} • {created} • {d}/{t}m/{s} servings/{c} ({n} ops)"
                            for (hid, created, d, t, s, c, n) in hist_rows]
        )
        if pick != "(select)":
            hid = int(pick.split("•")[0].strip()[1:])
            rec = get_history(engine, hid)
            if rec:
                _, created, d, t, s, c, n, snap, md = rec
                st.caption(f"Run at {created} | dietary={d}, time={t}m, servings={s}, cuisine={c}, options={n}")
                with st.expander("Pantry snapshot used"):
                    st.code(snap or "")
                st.markdown(md or "")
    else:
        st.caption("No history yet.")

# Pantry list
st.subheader("Pantry (from MySQL)")
items = list_ingredients(engine)
ranked = rank_ingredients(
    items,
    selected_diet=dietary,
    exclude_non_veg=exclude_non_veg,
    exclude_eggs=exclude_eggs,
    exclude_dairy=exclude_dairy,
)

if ranked:
    # Streamlit deprecates use_container_width → width="stretch"
    st.dataframe(
        [
            {k: v for k, v in it.items() if not k.startswith("_")}
            | {"days_left": round(it["_days_left"], 1), "priority": round(it["_priority"], 2)}
            for it in ranked
        ],
        width="stretch",
    )
else:
    st.info("No ingredients yet. Add some below!")

# Add / Update form
st.markdown("### Add / Update Ingredient")
with st.form("add_form"):
    name = st.text_input("Name *").strip().lower()
    qty = st.number_input("Quantity", min_value=0.0, value=1.0, step=0.5)
    unit = st.text_input("Unit", value="pcs")
    diet_type = st.selectbox("Diet type", ["veg","non-veg","eggs-ok","vegan","unknown"], index=0)
    category = st.selectbox("Category", ["veg","fruit","grain","dairy","protein","condiment","other"], index=0)
    expires = st.date_input("Expires on (optional)", value=date.today())
    submitted = st.form_submit_button("Save")
    if submitted:
        if not name:
            st.error("Name is required.")
        else:
            upsert_ingredient(engine, name, float(qty), unit, category, diet_type, expires.isoformat() if expires else None)
            st.success(f"Saved: {name}")
            st.rerun()


st.markdown("### Bulk add from text")
with st.expander("Paste a list (comma/newline separated)"):
    st.caption(
        "Examples:\n"
        "- `chicken breast 500g, eggs 6pcs, paneer 200g, tomato 3pcs`\n"
        "- Each line can be `name [qty][unit]`. Unknowns default to qty=1 and chosen unit.\n"
        "- Non-veg is auto-categorized under **protein** (chicken, fish, prawns, etc.)."
    )
    bulk_text = st.text_area("Items", height=140, placeholder="chicken breast 500g\neggs 6pcs\npaneer 200g\ntomato 3pcs")
    col1, col2, col3 = st.columns(3)
    with col1:
        default_unit = st.text_input("Default unit", value="pcs")
    with col2:
        default_days = st.number_input("Default days until expiry", min_value=0, max_value=60, value=3, step=1)
    with col3:
        do_overwrite = st.checkbox("Overwrite if exists", value=True, help="Always upsert by name.")

    if st.button("Add all"):
        lines = []
        # allow commas or newlines
        for part in (bulk_text or "").replace(",", "\n").splitlines():
            if part.strip():
                lines.append(part.strip())

        if not lines:
            st.warning("No lines found.")
        else:
            added = []
            for ln in lines:
                item = parse_line_to_item(ln, default_unit=default_unit, default_days=int(default_days))
                if not item:
                    continue
                # Upsert (existing logic already does overwrite via ON DUPLICATE KEY)
                upsert_ingredient(
                    engine,
                    name=item["name"],
                    qty=float(item["qty"]),
                    unit=item["unit"],
                    category=item["category"],
                    expires_on=item["expires_on"],
                )
                added.append(item["name"])
            if added:
                st.success(f"Added/updated: {', '.join(added)}")
                st.rerun()
            else:
                st.info("Nothing parsed from the input.")

# Delete
with st.expander("Delete an ingredient"):
    if items:
        choice = st.selectbox("Select", [f"{it['id']} • {it['name']} ({it['qty']}{it['unit']})" for it in items])
        if st.button("Delete selected"):
            del_id = int(choice.split("•")[0].strip())
            delete_ingredient(engine, del_id)
            st.success("Deleted.")
            st.rerun()
    else:
        st.caption("No items to delete.")

st.markdown("---")

# Generate recipes
st.header("✨ Generate Recipes from Pantry")
if st.button("Generate Recipes"):
    if not ranked:
        st.warning("Pantry is empty. Add some items first.")
    else:
        snap = snapshot_block(ranked)
        with st.spinner(f"Calling {OLLAMA_MODEL} via Ollama…"):
            try:
                md = generate_with_llm(
                    ranked_block=snap,
                    dietary=dietary,
                    time_limit=time_limit,
                    servings=servings,
                    cuisine=cuisine,
                    num_options=num_options,
                    exclude_non_veg=exclude_non_veg,
                    exclude_eggs=exclude_eggs,
                    exclude_dairy=exclude_dairy,
     )
                save_history(engine, {
                    "dietary": dietary,
                    "time_limit": time_limit,
                    "servings": servings,
                    "cuisine": cuisine,
                    "num_options": num_options
                }, snap, md)
                st.success("Recipes generated ✅")
                st.markdown(md)
                        # Parse usage JSON and show per-recipe "Use this" buttons
                usage = parse_usage_from_markdown(md)
                if not usage:
                    st.info("No usage_json block detected — cannot auto-deduct.")
                else:
                    st.subheader("Apply a recipe to pantry")
                    for idx, rec in enumerate(usage, start=1):
                        title = rec.get("title") or f"Recipe {idx}"
                        items = rec.get("items") or []
                        with st.expander(f"🧾 {title} — will deduct:"):
                            if items:
                                st.write([{k: v for k, v in it.items() if k in {"name","qty","unit"}} for it in items])
                            else:
                                st.caption("No pantry items listed.")
                            if st.button(f"Use this recipe (deduct)", key=f"use_{idx}"):
                                result = apply_usage(engine, items)
                                if result["updated"]:
                                    st.success(f"Updated: {[u['name'] for u in result['updated']]}")
                                if result["missing"]:
                                    st.warning(f"Missing in pantry (not deducted): {result['missing']}")
                                st.rerun()
            except Exception as e:
                st.error(f"LLM error: {e}")
                st.info("Tips:\n- Is Ollama running? (`ollama serve`)\n- Have you pulled the model? `ollama pull llama3.1:latest`\n- If Ollama is on a custom host/port, set OLLAMA_BASE_URL at the top of this file.")