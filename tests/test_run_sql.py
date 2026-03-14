import argparse
import sqlite3
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_sql  # noqa: E402


def make_args(**overrides):
    """
    测试里统一构造命令行参数，避免每个用例都手写一长串默认值。

    这里保留和真实脚本一致的字段名，后面如果参数表有调整，
    测试会第一时间暴露出不兼容，而不是悄悄失真。
    """
    values = {
        "allow_write": False,
        "confirm": "",
        "allow_full_table_write": False,
        "allow_bulk_write": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class RunSqlGuardTests(unittest.TestCase):
    def test_update_without_where_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "未检测到 WHERE"):
            run_sql.ensure_write_guard(
                "UPDATE member_user SET nickname = %s",
                "update",
                True,
                make_args(allow_write=True, confirm=run_sql.WRITE_CONFIRM_TOKEN),
            )

    def test_update_with_trivial_where_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "恒真表达式"):
            run_sql.ensure_write_guard(
                "UPDATE member_user SET nickname = %s WHERE (1 = 1)",
                "update",
                True,
                make_args(allow_write=True, confirm=run_sql.WRITE_CONFIRM_TOKEN),
            )

    def test_update_with_effective_where_is_allowed(self):
        run_sql.ensure_write_guard(
            "UPDATE member_user SET nickname = %s WHERE id = %s",
            "update",
            True,
            make_args(allow_write=True, confirm=run_sql.WRITE_CONFIRM_TOKEN),
        )

    def test_insert_select_requires_bulk_write_flag(self):
        with self.assertRaisesRegex(ValueError, "allow-bulk-write"):
            run_sql.ensure_write_guard(
                "INSERT INTO archive_user(id) SELECT id FROM member_user WHERE deleted = 1",
                "insert",
                True,
                make_args(allow_write=True, confirm=run_sql.WRITE_CONFIRM_TOKEN),
            )

    def test_extract_where_clause_ignores_subquery_limit(self):
        where_clause = run_sql.extract_top_level_where_clause(
            """
            UPDATE member_user
               SET nickname = %s
             WHERE id IN (
                   SELECT id
                     FROM member_user_log
                    WHERE action = 'rename'
                    LIMIT 10
             )
             RETURNING id
            """
        )
        self.assertEqual(
            where_clause,
            "id in ( select id from member_user_log where action = limit 10 )",
        )


class RunSqlPlaceholderTests(unittest.TestCase):
    def test_prepare_sqlite_positional_placeholders(self):
        sql, params = run_sql.prepare_sql_and_params(
            "sqlite",
            "SELECT * FROM member_user WHERE id = %s AND note = '%s'",
            [1001],
        )
        self.assertEqual(sql, "SELECT * FROM member_user WHERE id = ? AND note = '%s'")
        self.assertEqual(params, [1001])

    def test_prepare_sqlite_named_placeholders(self):
        sql, params = run_sql.prepare_sql_and_params(
            "sqlite",
            "SELECT * FROM member_user WHERE id = %(id)s AND note = '%(id)s'",
            {"id": 1001},
        )
        self.assertEqual(sql, "SELECT * FROM member_user WHERE id = :id AND note = '%(id)s'")
        self.assertEqual(params, {"id": 1001})

    def test_prepare_sqlite_named_placeholders_checks_missing_key(self):
        with self.assertRaisesRegex(ValueError, "命名参数缺失"):
            run_sql.prepare_sql_and_params(
                "sqlite",
                "SELECT * FROM member_user WHERE id = %(id)s",
                {"nickname": "alice"},
            )

    def test_prepare_sqlite_positional_placeholders_checks_count(self):
        with self.assertRaisesRegex(ValueError, "位置参数数量"):
            run_sql.prepare_sql_and_params(
                "sqlite",
                "SELECT * FROM member_user WHERE id = %s AND tenant_id = %s",
                [1001],
            )


class RunSqlExecuteTests(unittest.TestCase):
    def test_execute_sqlite_with_unified_placeholders(self):
        db_path = ROOT / "tests" / "tmp_run_sql.db"
        if db_path.exists():
            db_path.unlink()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE member_user(id INTEGER PRIMARY KEY, nickname TEXT)")
            conn.execute("INSERT INTO member_user(id, nickname) VALUES(?, ?)", (1001, "before"))
            conn.commit()
        finally:
            conn.close()

        db_config = {"engine": "sqlite", "database": str(db_path)}
        prepared_sql, prepared_params = run_sql.prepare_sql_and_params(
            "sqlite",
            "UPDATE member_user SET nickname = %s WHERE id = %s",
            ["after", 1001],
        )
        conn = run_sql.connect_database(db_config, timeout=5)
        try:
            affected_rows, rows = run_sql.execute_sql(conn, db_config, prepared_sql, prepared_params)
            conn.commit()
            self.assertEqual(affected_rows, 1)
            self.assertEqual(rows, [])

            verify_sql, verify_params = run_sql.prepare_sql_and_params(
                "sqlite",
                "SELECT nickname FROM member_user WHERE id = %(id)s",
                {"id": 1001},
            )
            _, verify_rows = run_sql.execute_sql(conn, db_config, verify_sql, verify_params)
            self.assertEqual(verify_rows[0]["nickname"], "after")
        finally:
            conn.close()
            if db_path.exists():
                db_path.unlink()


if __name__ == "__main__":
    unittest.main()
