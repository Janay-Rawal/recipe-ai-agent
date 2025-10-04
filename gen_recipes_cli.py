import os
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from langchain.prompts import ChatPromptTemplate
from langchain_community.llms import Ollama
from langchain.schema import StrOutputParser

load_dotenv()
DB_URL = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)

def days_left(iso_date: Optional[str]) -> float:
    if not iso_date:
        return 9_999.0
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return 9_999.0
    return (dt - datetime.now()).total_seconds() / 86400.0

def rank_ingredients(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = []
    for it in items:
        dleft = days_left(it.get("expires_on"))
        prio = 1.0 / max(dleft, 0.25)
        cat = (it.get("category") or "").lower()
        if cat in {"dairy", "protein", "veg", "vegetable", "fruit"}:
            prio *= 1.2
        ranked.append({**it, "_days_left": dleft, "_priority": prio})
    ranked.sort(key=lambda x: (-x["_priority"], x["name"]))
    return ranked

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

Rules:
- Prefer at least 2 of the top 4 expiring items when possible.
- Use mostly pantry items; mark any non-pantry as OPTIONAL.

Create {num_options} distinct recipes.
Return clean, readable markdown.
"""

def build_ranked_block(ranked: List[Dict[str, Any]], limit: int = 14) -> str:
    lines = []
    for i, it in enumerate(ranked[:limit], start=1):
        d = round(it["_days_left"], 1)
        lines.append(f"{i}. {it['name']} {it['qty']}{it['unit']} | exp ~ {d}d | prio={round(it['_priority'],2)}")
    return "\n".join(lines) if lines else "(empty)"

def fetch_ingredients(engine) -> List[Dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT id, name, qty, unit, category,
                   DATE_FORMAT(expires_on, '%Y-%m-%d') AS expires_on,
                   created_at, updated_at
            FROM ingredients
        """)).mappings().all()
        return [dict(r) for r in rows]

def ensure_tables(engine):
    DDL_INGREDIENTS = """
    CREATE TABLE IF NOT EXISTS ingredients (
      id INT AUTO_INCREMENT PRIMARY KEY,
      name VARCHAR(191) NOT NULL,
      qty DOUBLE DEFAULT 0,
      unit VARCHAR(64) DEFAULT '',
      category VARCHAR(64) DEFAULT '',
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

def save_history(engine, params: Dict[str, Any], snapshot: str, markdown: str):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO recipe_history (dietary, time_limit, servings, cuisine, num_options, ranked_snapshot, result_markdown)
            VALUES (:dietary, :time_limit, :servings, :cuisine, :num, :snap, :md)
        """), {
            "dietary": params["dietary"],
            "time_limit": params["time_limit"],
            "servings": params["servings"],
            "cuisine": params["cuisine"],
            "num": params["num_options"],
            "snap": snapshot,
            "md": markdown
        })

def main():
    parser = argparse.ArgumentParser(description="Generate recipes from pantry (MySQL → LangChain → Ollama).")
    parser.add_argument("--dietary", default="veg", help="none|veg|eggs-ok|vegan|non-veg")
    parser.add_argument("--time_limit", type=int, default=30)
    parser.add_argument("--servings", type=int, default=2)
    parser.add_argument("--cuisine", default="Indian")
    parser.add_argument("--num_options", type=int, default=2)
    parser.add_argument("--model", default="llama3.1:latest", help="Ollama model tag")
    args = parser.parse_args()

    try:
        from sqlalchemy import create_engine
        engine = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=180)
        ensure_tables(engine)
    except SQLAlchemyError as e:
        print("DB error:", e)
        return

    items = fetch_ingredients(engine)
    if not items:
        print("Pantry is empty. Add ingredients first (via your Streamlit UI).")
        return

    ranked = rank_ingredients(items)
    ranked_block = build_ranked_block(ranked)


    try:
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_RECIPE),
            ("user", USER_TEMPLATE),
        ])
        llm = Ollama(model=args.model)  
        chain = prompt | llm | StrOutputParser()

        user_vars = {
            "ranked": ranked_block,
            "dietary": args.dietary,
            "time_limit": args.time_limit,
            "servings": args.servings,
            "cuisine": args.cuisine,
            "num_options": args.num_options,
        }

        print("\n=== Prompt snapshot ===")
        print(USER_TEMPLATE.format(**user_vars))
        print("=======================\n")

        print("Asking model… (", args.model, ")")
        result_md = chain.invoke(user_vars)

        print("\n=== Recipes ===\n")
        print(result_md)

        save_history(engine, {
            "dietary": args.dietary,
            "time_limit": args.time_limit,
            "servings": args.servings,
            "cuisine": args.cuisine,
            "num_options": args.num_options,
        }, ranked_block, result_md)

        print("\n(Saved to recipe_history.)")

    except Exception as e:
        print("LLM error:", e)
        print("Tip: ensure Ollama is running and the model is pulled:")
        print("  1) ollama serve   (usually auto-starts)")
        print("  2) ollama pull", args.model)

if __name__ == "__main__":
    main()