import sqlite3
conn = sqlite3.connect("instance/store.db")
cur = conn.cursor()
cur.execute("DELETE FROM products WHERE slug LIKE \"customize-your-t-shirt-plain-white-%\"")
conn.commit()
conn.close()

