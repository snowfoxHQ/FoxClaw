import sqlite3
conn = sqlite3.connect('hestia_data/hkc/hkc.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print([r[0] for r in cur.fetchall()])
conn.close()
