"""
hkc-core / utils / id_gen.py
统一 ID 生成器。
每种类型有固定前缀，ID 格式：PREFIX_XXXXXXXX（8位补零）。
"""
import sqlite3
import threading


# 前缀映射
_PREFIX = {
    "Entity":   "ENT",
    "Concept":  "CON",
    "Fact":     "FCT",
    "Claim":    "CLM",
    "Evidence": "EVD",
    "Ability":  "ABL",
    "Relation": "REL",
    "Conflict": "CFT",
    "Event":    "EVT",
    "Skill":    "SKL",
    "Workflow": "WFL",
}


class IDGenerator:
    """
    线程安全的自增 ID 生成器。
    计数器持久化到 SQLite，重启后不重置。
    """

    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._db   = sqlite3.connect(db_path, check_same_thread=False)
        self._init_table()

    def _init_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS id_counters (
                prefix TEXT PRIMARY KEY,
                counter INTEGER DEFAULT 0
            )
        """)
        self._db.commit()

    def next(self, ku_type: str) -> str:
        prefix = _PREFIX.get(ku_type, "UNK")
        with self._lock:
            row = self._db.execute(
                "SELECT counter FROM id_counters WHERE prefix = ?",
                (prefix,)
            ).fetchone()

            if row:
                counter = row[0] + 1
                self._db.execute(
                    "UPDATE id_counters SET counter = ? WHERE prefix = ?",
                    (counter, prefix)
                )
            else:
                counter = 1
                self._db.execute(
                    "INSERT INTO id_counters (prefix, counter) VALUES (?, ?)",
                    (prefix, counter)
                )
            self._db.commit()

        return f"{prefix}_{counter:08d}"

    def next_batch(self, ku_type: str, count: int) -> list[str]:
        """一次性预留 count 个连续 ID（单事务、单 commit）。

        用于批量写关系等场景：逐个 next() 每次都 SELECT+UPDATE+commit(一次 fsync)，
        大文档关系发现产出上千条关系时会造成 fsync 风暴导致摄入长时间卡住。
        """
        if count <= 0:
            return []
        prefix = _PREFIX.get(ku_type, "UNK")
        with self._lock:
            row = self._db.execute(
                "SELECT counter FROM id_counters WHERE prefix = ?",
                (prefix,)
            ).fetchone()
            start = (row[0] if row else 0) + 1
            end   = start + count - 1
            if row:
                self._db.execute(
                    "UPDATE id_counters SET counter = ? WHERE prefix = ?",
                    (end, prefix)
                )
            else:
                self._db.execute(
                    "INSERT INTO id_counters (prefix, counter) VALUES (?, ?)",
                    (prefix, end)
                )
            self._db.commit()
        return [f"{prefix}_{c:08d}" for c in range(start, end + 1)]

    def peek(self, ku_type: str) -> int:
        """查看当前计数器，不自增。"""
        prefix = _PREFIX.get(ku_type, "UNK")
        with self._lock:      # 与 next() 共用同一连接,并发下读也要加锁
            row = self._db.execute(
                "SELECT counter FROM id_counters WHERE prefix = ?",
                (prefix,)
            ).fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """显式关闭 id_counters.db 连接。容器 close/reset 时必须调,
        否则句柄泄漏——Windows 下导致数据目录删不掉(WinError 32)。带锁关闭。"""
        with self._lock:
            self._db.close()
