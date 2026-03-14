#!/usr/bin/env python3
"""
数据库连接持久化存储层。

为什么单独抽这一层：
1. run_sql.py 和 db_registry.py 都要读写连接配置，抽成公共模块能避免重复逻辑导致行为不一致。
2. 连接信息属于“状态数据”，和执行 SQL 的“业务逻辑”分层后，后续扩展（加字段、迁移版本）更稳。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
SUPPORTED_ENGINES = {"sqlite", "mysql", "postgres"}
PASSWORD_SERVICE_NAME = "db-crud-guard"
ENGINE_DEFAULT_PORT = {
    "mysql": 3306,
    "postgres": 5432,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_registry_path(registry_path: Optional[str]) -> Path:
    """
    解析注册表文件路径。

    优先级：
    1) 显式 --registry
    2) 环境变量 DB_CRUD_GUARD_REGISTRY
    3) 当前工作目录 .db-crud-guard/registry.db
    """
    if registry_path:
        return Path(registry_path).expanduser().resolve()

    env_path = os.getenv("DB_CRUD_GUARD_REGISTRY")
    if env_path:
        return Path(env_path).expanduser().resolve()

    return (Path.cwd() / ".db-crud-guard" / "registry.db").resolve()


def open_registry(registry_path: Path) -> sqlite3.Connection:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(registry_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_registry(registry_path: Path) -> None:
    conn = open_registry(registry_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
              version INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS db_connection (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              engine TEXT NOT NULL,
              host TEXT,
              port INTEGER,
              database_name TEXT NOT NULL,
              username TEXT,
              password_ref TEXT,
              params_json TEXT NOT NULL DEFAULT '{}',
              is_default INTEGER NOT NULL DEFAULT 0,
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS ux_default_one
            ON db_connection(is_default) WHERE is_default = 1;
            """
        )
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version(version) VALUES(?)", (SCHEMA_VERSION,))
        conn.commit()
    finally:
        conn.close()


def _normalize_engine(engine: str) -> str:
    normalized = engine.strip().lower()
    if normalized not in SUPPORTED_ENGINES:
        raise ValueError(f"不支持的数据库引擎: {engine}")
    return normalized


def _parse_params_json(params_json: Optional[str]) -> str:
    if not params_json:
        return "{}"
    try:
        parsed = json.loads(params_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"params_json 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("params_json 仅支持 JSON 对象")
    return json.dumps(parsed, ensure_ascii=False)


def _load_keyring():
    try:
        import keyring  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("检测到需要存取密码，但未安装 keyring。请执行: python3 -m pip install --user keyring") from exc
    return keyring


def _set_password(password_ref: str, password: str) -> None:
    keyring = _load_keyring()
    keyring.set_password(PASSWORD_SERVICE_NAME, password_ref, password)


def _get_password(password_ref: str) -> str:
    keyring = _load_keyring()
    password = keyring.get_password(PASSWORD_SERVICE_NAME, password_ref)
    if password is None:
        raise RuntimeError(f"未找到密码凭据: {password_ref}。请重新执行连接配置更新密码。")
    return password


def _delete_password(password_ref: str) -> None:
    keyring = _load_keyring()
    try:
        keyring.delete_password(PASSWORD_SERVICE_NAME, password_ref)
    except Exception:
        # 删除密码属于“清理动作”，失败不应该阻塞主流程，避免数据删除半途失败。
        pass


def _row_to_public_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "engine": row["engine"],
        "host": row["host"],
        "port": row["port"],
        "database_name": row["database_name"],
        "username": row["username"],
        "params_json": json.loads(row["params_json"] or "{}"),
        "is_default": bool(row["is_default"]),
        "enabled": bool(row["enabled"]),
        "has_password": bool(row["password_ref"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def add_connection(
    registry_path: Path,
    *,
    name: str,
    engine: str,
    database_name: str,
    host: Optional[str],
    port: Optional[int],
    username: Optional[str],
    password: Optional[str],
    params_json: Optional[str],
    set_default: bool,
    enabled: bool = True,
) -> Dict[str, Any]:
    normalized_engine = _normalize_engine(engine)
    if not name.strip():
        raise ValueError("name 不能为空")
    if not database_name.strip():
        raise ValueError("database_name 不能为空")
    if normalized_engine in {"mysql", "postgres"} and (not host or not username):
        raise ValueError(f"{normalized_engine} 连接必须提供 host 和 username")

    final_port = port
    if normalized_engine in ENGINE_DEFAULT_PORT and final_port is None:
        final_port = ENGINE_DEFAULT_PORT[normalized_engine]

    params_json_text = _parse_params_json(params_json)
    now = utc_now_iso()
    conn_id = str(uuid.uuid4())
    password_ref = None

    conn = open_registry(registry_path)
    try:
        exists = conn.execute("SELECT 1 FROM db_connection WHERE name = ?", (name,)).fetchone()
        if exists:
            raise ValueError(f"连接名已存在: {name}")

        if password is not None:
            password_ref = f"conn:{conn_id}"
            _set_password(password_ref, password)

        total_count = conn.execute("SELECT COUNT(1) AS cnt FROM db_connection").fetchone()["cnt"]
        final_default = set_default or total_count == 0
        if final_default:
            conn.execute("UPDATE db_connection SET is_default = 0")

        conn.execute(
            """
            INSERT INTO db_connection (
              id, name, engine, host, port, database_name, username, password_ref,
              params_json, is_default, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conn_id,
                name,
                normalized_engine,
                host,
                final_port,
                database_name,
                username,
                password_ref,
                params_json_text,
                1 if final_default else 0,
                1 if enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM db_connection WHERE id = ?", (conn_id,)).fetchone()
        return _row_to_public_dict(row)
    except Exception:
        if password_ref:
            _delete_password(password_ref)
        raise
    finally:
        conn.close()


def list_connections(registry_path: Path) -> List[Dict[str, Any]]:
    conn = open_registry(registry_path)
    try:
        rows = conn.execute("SELECT * FROM db_connection ORDER BY created_at ASC").fetchall()
        return [_row_to_public_dict(row) for row in rows]
    finally:
        conn.close()


def get_connection(registry_path: Path, *, name: str) -> Optional[Dict[str, Any]]:
    conn = open_registry(registry_path)
    try:
        row = conn.execute("SELECT * FROM db_connection WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return _row_to_public_dict(row)
    finally:
        conn.close()


def set_default_connection(registry_path: Path, *, name: str) -> Dict[str, Any]:
    conn = open_registry(registry_path)
    try:
        row = conn.execute("SELECT * FROM db_connection WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"连接不存在: {name}")
        conn.execute("UPDATE db_connection SET is_default = 0")
        conn.execute(
            "UPDATE db_connection SET is_default = 1, updated_at = ? WHERE name = ?",
            (utc_now_iso(), name),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM db_connection WHERE name = ?", (name,)).fetchone()
        return _row_to_public_dict(updated)
    finally:
        conn.close()


def remove_connection(registry_path: Path, *, name: str) -> None:
    conn = open_registry(registry_path)
    try:
        row = conn.execute("SELECT * FROM db_connection WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"连接不存在: {name}")

        conn.execute("DELETE FROM db_connection WHERE name = ?", (name,))
        conn.commit()

        if row["password_ref"]:
            _delete_password(row["password_ref"])

        if row["is_default"]:
            next_row = conn.execute(
                "SELECT name FROM db_connection WHERE enabled = 1 ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if next_row:
                set_default_connection(registry_path, name=next_row["name"])
    finally:
        conn.close()


def update_connection(
    registry_path: Path,
    *,
    name: str,
    database_name: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    username: Optional[str] = None,
    enabled: Optional[bool] = None,
    params_json: Optional[str] = None,
    set_default: bool = False,
    password: Optional[str] = None,
    clear_password: bool = False,
) -> Dict[str, Any]:
    conn = open_registry(registry_path)
    try:
        row = conn.execute("SELECT * FROM db_connection WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"连接不存在: {name}")

        updates: Dict[str, Any] = {"updated_at": utc_now_iso()}
        if database_name is not None:
            if not database_name.strip():
                raise ValueError("database_name 不能为空")
            updates["database_name"] = database_name
        if host is not None:
            updates["host"] = host
        if port is not None:
            updates["port"] = port
        if username is not None:
            updates["username"] = username
        if enabled is not None:
            updates["enabled"] = 1 if enabled else 0
        if params_json is not None:
            updates["params_json"] = _parse_params_json(params_json)

        old_password_ref = row["password_ref"]
        new_password_ref = old_password_ref
        if password is not None:
            if not old_password_ref:
                new_password_ref = f"conn:{row['id']}"
            _set_password(new_password_ref, password)
            updates["password_ref"] = new_password_ref
        elif clear_password:
            if old_password_ref:
                _delete_password(old_password_ref)
            updates["password_ref"] = None

        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [name]
        conn.execute(f"UPDATE db_connection SET {set_clause} WHERE name = ?", values)

        if set_default:
            conn.execute("UPDATE db_connection SET is_default = 0")
            conn.execute("UPDATE db_connection SET is_default = 1 WHERE name = ?", (name,))

        conn.commit()
        updated = conn.execute("SELECT * FROM db_connection WHERE name = ?", (name,)).fetchone()
        return _row_to_public_dict(updated)
    finally:
        conn.close()


def get_connection_runtime_config(registry_path: Path, *, name: Optional[str] = None) -> Dict[str, Any]:
    """
    获取 run_sql 可直接消费的运行时配置。

    设计上这里返回“已补齐密码”的配置，是为了让 run_sql 不需要关心密码存放细节，
    这样 SQL 执行逻辑可以保持单一职责。
    """
    conn = open_registry(registry_path)
    try:
        if name:
            row = conn.execute("SELECT * FROM db_connection WHERE name = ? AND enabled = 1", (name,)).fetchone()
            if row is None:
                raise ValueError(f"连接不存在或已禁用: {name}")
        else:
            row = conn.execute(
                "SELECT * FROM db_connection WHERE is_default = 1 AND enabled = 1 LIMIT 1"
            ).fetchone()
            if row is None:
                raise ValueError("未配置默认连接，请先设置默认连接或显式传 --conn")

        password = None
        if row["password_ref"]:
            password = _get_password(row["password_ref"])

        return {
            "name": row["name"],
            "engine": row["engine"],
            "host": row["host"],
            "port": row["port"],
            "database": row["database_name"],
            "user": row["username"],
            "password": password,
            "params": json.loads(row["params_json"] or "{}"),
        }
    finally:
        conn.close()
