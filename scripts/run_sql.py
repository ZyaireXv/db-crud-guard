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
SQLITE_PLACEHOLDER_PREFIX = ":"
TOP_LEVEL_CLAUSE_BOUNDARIES = ("order by", "limit", "returning", "offset")
TRIVIAL_TRUE_WHERE_CLAUSES = {
    "1=1",
    "1 = 1",
    "true",
    "1",
    "not false",
}


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
    parser.add_argument(
        "--allow-bulk-write",
        action="store_true",
        help="允许 INSERT ... SELECT / REPLACE ... SELECT 这类批量写入（高风险）",
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


def mask_sql_literals_and_comments(sql: str) -> str:
    """
    把字符串字面量和注释替换成空格，但保留原始长度。

    这样做的目的不是“解析完整 SQL 语法”，而是先把最容易干扰判断的内容屏蔽掉，
    后面的关键字检测、占位符替换、WHERE 安全检查就不会被注释和字符串常量误导。
    """
    chars: List[str] = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    i = 0

    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if in_line_comment:
            chars.append("\n" if ch == "\n" else " ")
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            chars.append("\n" if ch == "\n" else " ")
            if ch == "*" and nxt == "/":
                chars.append(" ")
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue

        if in_single_quote:
            chars.append("\n" if ch == "\n" else " ")
            if ch == "'" and nxt == "'":
                chars.append(" ")
                i += 2
                continue
            if ch == "'":
                in_single_quote = False
            i += 1
            continue

        if in_double_quote:
            chars.append("\n" if ch == "\n" else " ")
            if ch == '"' and nxt == '"':
                chars.append(" ")
                i += 2
                continue
            if ch == '"':
                in_double_quote = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            chars.append(" ")
            chars.append(" ")
            i += 2
            in_line_comment = True
            continue

        if ch == "/" and nxt == "*":
            chars.append(" ")
            chars.append(" ")
            i += 2
            in_block_comment = True
            continue

        if ch == "'":
            chars.append(" ")
            in_single_quote = True
            i += 1
            continue

        if ch == '"':
            chars.append(" ")
            in_double_quote = True
            i += 1
            continue

        chars.append(ch)
        i += 1

    return "".join(chars)


def normalize_masked_sql(sql: str) -> str:
    """
    统一把 SQL 压平成便于比较的形式。

    这里故意先做屏蔽再规范化空白，避免像 "where" 出现在字符串里时被误判成真实条件。
    """
    return re.sub(r"\s+", " ", mask_sql_literals_and_comments(sql).strip().lower())


def classify_sql(sql: str) -> Tuple[str, bool]:
    clean = remove_leading_comments(sql)
    match = re.match(r"([a-zA-Z_]+)", clean)
    if not match:
        raise ValueError("无法识别 SQL 类型，请检查 SQL 文本")
    keyword = match.group(1).lower()
    if keyword not in ALLOWED_KEYWORDS:
        raise ValueError("仅支持 SELECT/INSERT/UPDATE/DELETE/REPLACE，已拦截非 CRUD 语句")
    return keyword, keyword in WRITE_KEYWORDS


def strip_balanced_wrapping_parentheses(text: str) -> str:
    """
    去掉最外层一对一对包裹的括号。

    例如 `((1 = 1))` 会被化简成 `1 = 1`，这样后面的“恒真条件”判断才不会漏掉。
    """
    candidate = text.strip()
    while candidate.startswith("(") and candidate.endswith(")"):
        depth = 0
        wrapped = True
        for index, ch in enumerate(candidate):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and index != len(candidate) - 1:
                    wrapped = False
                    break
        if not wrapped:
            break
        candidate = candidate[1:-1].strip()
    return candidate


def extract_top_level_where_clause(sql: str) -> Optional[str]:
    """
    提取 UPDATE/DELETE 顶层 WHERE 子句文本。

    这里不追求完整 AST 级解析，只处理我们关心的顶层结构：
    1. 先屏蔽字符串和注释，降低误判概率。
    2. 再按括号层级扫描，尽量避开子查询里的 ORDER BY / LIMIT 干扰。
    """
    masked_sql = mask_sql_literals_and_comments(sql)
    lowered_sql = masked_sql.lower()
    where_match = re.search(r"\bwhere\b", lowered_sql)
    if not where_match:
        return None

    start = where_match.end()
    depth = 0
    end = len(lowered_sql)
    i = start

    while i < len(lowered_sql):
        ch = lowered_sql[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if depth == 0:
            for boundary in TOP_LEVEL_CLAUSE_BOUNDARIES:
                if lowered_sql.startswith(boundary, i):
                    prev_ok = i == 0 or lowered_sql[i - 1].isspace()
                    next_index = i + len(boundary)
                    next_ok = next_index >= len(lowered_sql) or lowered_sql[next_index].isspace()
                    if prev_ok and next_ok:
                        end = i
                        i = len(lowered_sql)
                        break
        i += 1

    clause = lowered_sql[start:end].strip()
    if not clause:
        return None
    return re.sub(r"\s+", " ", clause)


def is_trivial_true_where_clause(where_clause: str) -> bool:
    """
    识别最常见的“看起来有 WHERE，实际上还是全表”的条件。

    我们这里只拦截纯恒真条件，不去冒进拦截更复杂的表达式，
    目的是先把明显危险的情况收住，同时尽量避免误伤正常 SQL。
    """
    normalized = strip_balanced_wrapping_parentheses(where_clause)
    return normalized in TRIVIAL_TRUE_WHERE_CLAUSES


def is_bulk_source_write(sql: str, keyword: str) -> bool:
    """
    检测 INSERT ... SELECT / REPLACE ... SELECT 这类批量写入。

    这类语句即便写得合法，也往往一次性影响大量数据，
    和普通单行 INSERT 不是一个风险等级，所以单独要求额外放行。
    """
    if keyword not in {"insert", "replace"}:
        return False
    normalized = normalize_masked_sql(sql)
    if " values " in f" {normalized} ":
        return False
    return " select " in f" {normalized} "


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

    # UPDATE/DELETE 默认必须带有效 WHERE，避免误改/误删整表。
    if keyword in {"update", "delete"} and not args.allow_full_table_write:
        where_clause = extract_top_level_where_clause(sql)
        if where_clause is None:
            raise ValueError("UPDATE/DELETE 未检测到 WHERE，已拦截。若确需全表操作，请显式传 --allow-full-table-write")
        if is_trivial_true_where_clause(where_clause):
            raise ValueError(
                "UPDATE/DELETE 的 WHERE 条件为恒真表达式，仍等价于全表写入，已拦截。若确需执行，请显式传 --allow-full-table-write"
            )

    # INSERT ... SELECT / REPLACE ... SELECT 容易一次性写入大量数据，单独要求显式放行。
    if is_bulk_source_write(sql, keyword) and not args.allow_bulk_write:
        raise ValueError(
            "检测到 INSERT ... SELECT / REPLACE ... SELECT 批量写入，已拦截。若确认范围无误，请显式传 --allow-bulk-write"
        )


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
        db_config["driver"] = "sqlite3"
        conn = sqlite3.connect(db_config["database"], timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    if db_config["engine"] == "mysql":
        require_network_args(db_config)
        try:
            import pymysql
        except ModuleNotFoundError as exc:
            raise RuntimeError("未安装 pymysql，请先执行: python3 -m pip install --user pymysql") from exc

        db_config["driver"] = "pymysql"
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
            from psycopg.rows import dict_row  # type: ignore

            db_config["driver"] = "psycopg"
            return psycopg.connect(
                host=db_config["host"],
                port=db_config["port"] or 5432,
                user=db_config["user"],
                password=db_config["password"],
                dbname=db_config["database"],
                connect_timeout=timeout,
                row_factory=dict_row,
            )
        except ModuleNotFoundError:
            try:
                import psycopg2  # type: ignore
                import psycopg2.extras  # type: ignore
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "未安装 PostgreSQL 驱动，请先安装 psycopg 或 psycopg2: python3 -m pip install --user psycopg"
                ) from exc
            db_config["driver"] = "psycopg2"
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


def replace_sqlite_named_placeholders(sql: str) -> Tuple[str, List[str]]:
    """
    把统一写法 `%(name)s` 转成 SQLite 可直接执行的 `:name`。

    之所以选 `%(... )s` 作为统一写法，是因为 MySQL/PostgreSQL 原生就支持，
    只需要在 SQLite 这里做一次转换，就能让文档和调用姿势保持一致。
    """
    masked_sql = mask_sql_literals_and_comments(sql)
    result: List[str] = []
    used_names: List[str] = []
    index = 0

    while index < len(sql):
        if masked_sql.startswith("%(", index):
            match = re.match(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s", sql[index:])
            if match:
                name = match.group(1)
                result.append(f"{SQLITE_PLACEHOLDER_PREFIX}{name}")
                used_names.append(name)
                index += len(match.group(0))
                continue
        result.append(sql[index])
        index += 1

    return "".join(result), used_names


def replace_sqlite_positional_placeholders(sql: str) -> Tuple[str, int]:
    """
    把统一写法 `%s` 转成 SQLite 的 `?`。

    这里只替换真实 SQL 代码中的占位符，不碰字符串和注释，避免把普通文本误改掉。
    """
    masked_sql = mask_sql_literals_and_comments(sql)
    result: List[str] = []
    count = 0
    index = 0

    while index < len(sql):
        if masked_sql.startswith("%s", index):
            result.append("?")
            count += 1
            index += 2
            continue
        result.append(sql[index])
        index += 1

    return "".join(result), count


def prepare_sql_and_params(engine: str, sql: str, params: Optional[Any]) -> Tuple[str, Optional[Any]]:
    """
    把“统一入口”的参数写法整理成具体驱动能执行的形式。

    约定如下：
    1. 位置参数统一用 `%s`
    2. 命名参数统一用 `%(name)s`
    3. SQLite 在执行前自动转换成 `?` / `:name`

    这样调用方不用再记住每个驱动各自的小差异，真正做到同一套调用姿势跨库复用。
    """
    if params is None:
        return sql, None

    if engine != "sqlite":
        return sql, params

    if isinstance(params, list):
        prepared_sql, placeholder_count = replace_sqlite_positional_placeholders(sql)
        if placeholder_count and placeholder_count != len(params):
            raise ValueError(
                f"SQL 中位置参数数量({placeholder_count})与 --params-json 数组长度({len(params)})不一致"
            )
        return prepared_sql, params

    if isinstance(params, dict):
        prepared_sql, used_names = replace_sqlite_named_placeholders(sql)
        missing_names = [name for name in used_names if name not in params]
        if missing_names:
            raise ValueError(f"命名参数缺失: {', '.join(sorted(set(missing_names)))}")
        return prepared_sql, params

    return sql, params


def create_cursor(conn, db_config: Dict[str, Any]):
    """
    为不同驱动显式选定游标行为，避免依赖第三方库默认值。

    PostgreSQL 这里尤其重要：psycopg3 和 psycopg2 的默认返回行格式并不完全一致，
    如果不收口，后续同一段 rows_to_dicts 逻辑就可能出现“今天能跑，换个驱动又飘了”的问题。
    """
    if db_config["engine"] == "postgres" and db_config.get("driver") == "psycopg2":
        import psycopg2.extras  # type: ignore

        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor()


def execute_sql(conn, db_config: Dict[str, Any], sql: str, params: Optional[Any]) -> Tuple[int, List[Dict[str, Any]]]:
    cursor = create_cursor(conn, db_config)
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
        prepared_sql, prepared_params = prepare_sql_and_params(db_config["engine"], sql, params)

        conn = connect_database(db_config, timeout=args.timeout)
        affected_rows, rows = execute_sql(conn, db_config, prepared_sql, prepared_params)
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
