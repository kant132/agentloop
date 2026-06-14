"""Build a small fake codegraph SQLite DB for tests.

Per TDD.md Appendix B:
- nodes table: 1 route, 3 methods, 2 classes, 1 field
- edges table: 2 calls + 1 contains + 1 references
- Chain: m:entry -> m:1 -> m:2 (depth 3)
"""
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "codegraph-fake.db")
if os.path.exists(DB):
    os.remove(DB)

con = sqlite3.connect(DB)
c = con.cursor()
c.executescript("""
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    kind TEXT,
    name TEXT,
    qualified_name TEXT,
    file_path TEXT,
    start_line INT,
    end_line INT,
    signature TEXT,
    decorators TEXT
);
CREATE TABLE edges (
    source TEXT,
    target TEXT,
    kind TEXT,
    line INT,
    col INT
);
INSERT INTO nodes VALUES
  ('r:1','route','GET /api/u','GET /api/u','Controller.java',10,10,'',NULL),
  ('m:entry','method','listUsers','pkg::C::m','Controller.java',10,30,'',NULL),
  ('m:1','method','findAll','pkg::S::m','Service.java',5,25,'',NULL),
  ('m:2','method','executeQuery','pkg::Dao::executeQuery','Dao.java',1,15,'',NULL),
  ('m:3','method','RuntimeExec','pkg::Util::RuntimeExec','Util.java',1,10,'',NULL),
  ('c:1','class','Controller','pkg::Controller','Controller.java',1,40,'',NULL),
  ('c:2','class','Service','pkg::Service','Service.java',1,30,'',NULL),
  ('f:1','field','mapper','pkg::Service::mapper','Service.java',3,3,'',NULL);
INSERT INTO edges VALUES
  ('m:entry','m:1','calls',15,8),
  ('m:1','m:2','calls',12,8),
  ('c:1','m:entry','contains',NULL,NULL),
  ('c:2','m:1','contains',NULL,NULL),
  ('m:1','m:1','calls',20,5),
  ('m:2','m:3','calls',5,5);
""")
con.commit()
con.close()
print("Built:", DB)
