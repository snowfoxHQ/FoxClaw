import sqlite3
conn = sqlite3.connect('hestia_data/hkc/hkc.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM knowledge_units')
print('KU count:', cur.fetchone()[0])
cur.execute('SELECT COUNT(*) FROM claims')
print('Claim count:', cur.fetchone()[0])
conn.close()
