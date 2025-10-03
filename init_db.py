import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
url = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)
engine = create_engine(url, pool_pre_ping=True)

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
    print("Tables ensured.")