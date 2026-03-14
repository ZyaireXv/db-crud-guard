#!/usr/bin/env python3
"""
统一的数据库 SQL 执行脚本。
支持 SQLite / MySQL / PostgreSQL，并提供默认安全护栏。
"""

import argparse
import base64
import datetime
import decimal
import json
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

from registry_store import get_connection_runtime_config, init_registry, resolve_registry_path

WRITE_CONFIRM_TOKEN = "CONFIRM_WRITE"
WRITE_KEYWORDS = {"insert", "update", "delete", "replace"}
ALLOWED_KEYWORDS = {"select", "insert", "update", "delete", "replace"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行单条 SQL，并输出结构化结果。")
    parser.add_argument("--conn", help="使用持久化连接名（由 db_registry.py 管理）")
    parser.add_argument("--registry", help="连接注册表路径，默认 .db-crud-guard/registry.db")
    parser.add_argument("--engine", choices=["sqlite", "mysql", "postgres"])
    parser.add_argument("--database", help="SQLite 文件路径或数据库名")
    parser.add_argument("--host", help="MySQL/PostgreSQL 主机地址")
    parser.add_argument("--port", type=int, help="MySQL/PostgreSQL 端口")
    parser.add_argument("--user", help="MySQL/PostgreSQL 用户名")
    parser.add_argument("--password", help="MySQL/PostgreSQL 密码")
    parser.add_argument("--sql", help="要执行的 SQL 文本")
    parser.add_argument("--sql-file", help="从文件读取 SQL")
    parser.add_argument("--params-json", help="JSON 格式参数，支持列表或对象")
    parser.add_argument("--timeout", type=int, default=30, help="连接超时秒数")
    parser.add_argument("--allow-write", action="store_true", help="允许执行写操作")
    parser.add_argument(
        "--confirm",
        default="",
        help=f"写操作确认口令，必须等于 {WRITE_CONFIRM_TOKEN}",
    )
    parser.add_argument(
        "--allow-full-table-write",
        action="store_true",
        help="允许无 WHERE 的 UPDATE/DELETE（高风险）",
    )
    return parser.parse_args()


def load_sql(args: argparse.Namespace) -> str:
    if bool(args.sql) == bool(args.sql_file):
        raise ValueError("必须二选一：--sql 或 --sql-file")
    if args.sql:
        return args.sql.strip()
    with open(args.sql_file, "r", encoding="utf-8") as file:
        return file.read().strip()


def parse_params(params_json: Optional[str]) -> Optional[Any]:
    if not params_json:
        return None
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--params-json 不是合法 JSON: {exc}") from exc
    if not isinstance(params, (list, dict)):
        raise ValueError("--params-json 仅支持 JSON 数组或 JSON 对象")
    return params


def split_sql_statements(sql: str) -> List[str]:
    """
    只允许执行单条 SQL。
    这里不用简单 split(';')，是因为字符串常量里可能包含分号，直接切分会误判。
    """
    statements: List[str] = []
    current: List[str] = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    i = 0

    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if in_line_comment:
            current.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            current.append(ch)
            if ch == "*" and nxt == "/":
                current.append(nxt)
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue

        if not in_single_quote and not in_double_quote and ch == "-" and nxt == "-":
            current.append(ch)
            current.append(nxt)
            i += 2
            in_line_comment = True
            continue

        if not in_single_quote and not in_double_quote and ch == "/" and nxt == "*":
            current.append(ch)
            current.append(nxt)
            i += 2
            in_block_comment = True
            continue

        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(ch)
            i += 1
            continue

        if ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(ch)
            i += 1
            continue

        if ch == ";" and not in_single_quote and not in_double_quote:
            text = "".join(current).strip()
            if text:
                statements.append(text)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def remove_leading_comments(sql: str) -> str:
    """
    为了准确识别语句类型，需要先跳过开头注释。
    """
    text = sql.lstrip()
    while True:
        if text.startswith("--"):
            end = text.find("\n")
            if end == -1:
                return ""
            text = text[end + 1 :].lstrip()
            continue
        if text.startswith("/*"):
            end = text.find("*/")
            if end == -1:
                return ""
            text = text[end + 2 :].lstrip()
            continue
        return text


def classify_sql(sql: str) -> Tuple[str, bool]:
    clean = remove_leading_comments(sql)
    match = re.match(r"([a-zA-Z_]+)", clean)
    if not match:
        raise ValueError("无法识别 SQL 类型，请检查 SQL 文本")
    keyword = match.group(1).lower()
    if keyword not in ALLOWED_KEYWORDS:
        raise ValueError("仅支持 SELECT/INSERT/UPDATE/DELETE/REPLACE，已拦截非 CRUD 语句")
    return keyword, keyword in WRITE_KEYWORDS


def ensure_single_statement(sql: str) -> None:
    statements = split_sql_statements(sql)
    if len(statements) == 0:
        raise ValueError("SQL 为空，未检测到可执行语句")
    if len(statements) > 1:
        raise ValueError("只允许执行单条 SQL，多语句已拦截")


def ensure_write_guard(sql: str, keyword: str, is_write: bool, args: argparse.Namespace) -> None:
    if not is_write:
        return
    # 写操作要求双重确认：显式放行 + 固定确认口令，降低误操作概率。
    if not args.allow_write:
        raise ValueError("检测到写操作，请添加 --allow-write")
    if args.confirm != WRITE_CONFIRM_TOKEN:
        raise ValueError(f"检测到写操作，请添加 --confirm {WRITE_CONFIRM_TOKEN}")

    # UPDATE/DELETE 默认必须带 WHERE，避免误改/误删整表。
    if keyword in {"update", "delete"} and not args.allow_full_table_write:
        normalized = re.sub(r"\s+", " ", sql.strip().lower())
        if " where " not in f" {normalized} ":
            raise ValueError("UPDATE/DELETE 未检测到 WHERE，已拦截。若确需全表操作，请显式传 --allow-full-table-write")


def require_network_args(db_config: Dict[str, Any]) -> None:
    missing = [name for name in ("host", "user", "password") if not db_config.get(name)]
    if missing:
        raise ValueError(f"{db_config['engine']} 连接缺少参数: {', '.join(missing)}")


def build_db_config(args: argparse.Namespace) -> Dict[str, Any]:
    """
    构造数据库连接配置，支持两种输入模式：
    1) 直连参数模式：--engine/--database...
    2) 持久化连接模式：--conn（从注册表加载）
    """
    if args.conn:
        # --conn 模式不允许再混用连接参数，避免“谁覆盖谁”的歧义导致误连库。
        mixed_args = ["engine", "database", "host", "port", "user", "password"]
        used_mixed = [key for key in mixed_args if getattr(args, key) not in (None, "")]
        if used_mixed:
            raise ValueError(f"--conn 模式下不允许混用连接参数: {', '.join(used_mixed)}")

        registry_path = resolve_registry_path(args.registry)
        init_registry(registry_path)
        return get_connection_runtime_config(registry_path, name=args.conn)

    direct_mode_fields = ["engine", "database", "host", "port", "user", "password"]
    used_direct_mode = any(getattr(args, key) not in (None, "") for key in direct_mode_fields)
    if not used_direct_mode:
        # 未提供任何直连参数时，自动走默认连接，减少重复输入连接信息。
        registry_path = resolve_registry_path(args.registry)
        init_registry(registry_path)
        return get_connection_runtime_config(registry_path, name=None)

    if not args.engine or not args.database:
        raise ValueError("直连模式必须提供 --engine 和 --database；或使用 --conn/默认连接模式")

    return {
        "name": None,
        "engine": args.engine,
        "host": args.host,
        "port": args.port,
        "database": args.database,
        "user": args.user,
        "password": args.password,
        "params": {},
    }


def connect_database(db_config: Dict[str, Any], timeout: int):
    if db_config["engine"] == "sqlite":
        conn = sqlite3.connect(db_config["database"], timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    if db_config["engine"] == "mysql":
        require_network_args(db_config)
        try:
            import pymysql
        except ModuleNotFoundError as exc:
            raise RuntimeError("未安装 pymysql，请先执行: python3 -m pip install --user pymysql") from exc

        conn = pymysql.connect(
            host=db_config["host"],
            port=db_config["port"] or 3306,
            user=db_config["user"],
            password=db_config["password"],
            database=db_config["database"],
            connect_timeout=timeout,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        return conn

    if db_config["engine"] == "postgres":
        require_network_args(db_config)
        try:
            import psycopg  # type: ignore

            return psycopg.connect(
                host=db_config["host"],
                port=db_config["port"] or 5432,
                user=db_config["user"],
                password=db_config["password"],
                dbname=db_config["database"],
                connect_timeout=timeout,
            )
        except ModuleNotFoundError:
            try:
                import psycopg2  # type: ignore
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "未安装 PostgreSQL 驱动，请先安装 psycopg 或 psycopg2: python3 -m pip install --user psycopg"
                ) from exc
            return psycopg2.connect(
                host=db_config["host"],
                port=db_config["port"] or 5432,
                user=db_config["user"],
                password=db_config["password"],
                dbname=db_config["database"],
                connect_timeout=timeout,
            )

    raise ValueError(f"不支持的引擎: {db_config['engine']}")


def rows_to_dicts(rows: List[Any], columns: List[str]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            result.append(row)
            continue
        if hasattr(row, "keys"):
            result.append({k: row[k] for k in row.keys()})
            continue
        result.append(dict(zip(columns, row)))
    return result


def json_default(value: Any) -> Any:
    """
    统一处理数据库常见“非 JSON 原生类型”。
    这里集中做转换，能保证脚本所有 JSON 输出行为一致，避免某个分支忘记处理再次报错。
    """
    if isinstance(value, datetime.datetime):
        # datetime 按 ISO 格式输出，既保留精度，也便于机器和人类同时阅读。
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        # Decimal 转字符串而不是 float，避免金额等高精度字段丢精度。
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        # 二进制字段用 base64 输出，保证可逆且不会因为乱码导致 JSON 非法。
        return base64.b64encode(bytes(value)).decode("ascii")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dump_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=json_default)


def execute_sql(conn, sql: str, params: Optional[Any]) -> Tuple[int, List[Dict[str, Any]]]:
    cursor = conn.cursor()
    try:
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)

        if cursor.description:
            columns = [item[0] for item in cursor.description]
            rows = cursor.fetchall()
            return cursor.rowcount, rows_to_dicts(list(rows), columns)
        return cursor.rowcount, []
    finally:
        cursor.close()


def main() -> int:
    args = parse_args()
    conn = None
    sql = ""
    is_write = False
    keyword = ""
    db_config: Dict[str, Any] = {}

    try:
        db_config = build_db_config(args)
        sql = load_sql(args)
        ensure_single_statement(sql)
        params = parse_params(args.params_json)
        keyword, is_write = classify_sql(sql)
        ensure_write_guard(sql, keyword, is_write, args)

        conn = connect_database(db_config, timeout=args.timeout)
        affected_rows, rows = execute_sql(conn, sql, params)
        if is_write:
            conn.commit()

        # 统一输出 JSON，便于后续自动化流程直接消费，不需要再解析人类文本。
        print(
            dump_json(
                {
                    "ok": True,
                    "connection_name": db_config.get("name"),
                    "engine": db_config["engine"],
                    "statement_type": keyword,
                    "is_write": is_write,
                    "affected_rows": affected_rows,
                    "returned_rows": len(rows),
                    "rows": rows,
                }
            )
        )
        return 0
    except Exception as exc:
        if conn is not None and is_write:
            try:
                conn.rollback()
            except Exception:
                pass
        print(
            dump_json(
                {
                    "ok": False,
                    "connection_name": db_config.get("name"),
                    "statement_type": keyword or None,
                    "is_write": is_write,
                    "error": str(exc),
                }
            )
        )
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
