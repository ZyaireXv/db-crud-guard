#!/usr/bin/env python3
"""
数据库连接注册表管理脚本。

核心目标：
1. 把“连接配置管理”从 run_sql.py 拆出来，避免执行脚本承担太多状态管理职责。
2. 支持数据库列表持久化（可添加一个或多个连接），后续只用连接名执行 SQL。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

from registry_store import (
    add_connection,
    get_connection,
    get_connection_runtime_config,
    init_registry,
    list_connections,
    remove_connection,
    resolve_registry_path,
    set_default_connection,
    update_connection,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="管理 db-crud-guard 数据库连接注册表。")
    parser.add_argument("--registry", help="注册表路径，默认 .db-crud-guard/registry.db")

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_cmd = subparsers.add_parser("add", help="新增连接配置")
    add_cmd.add_argument("--name", required=True, help="连接名，后续 run_sql 通过 --conn 使用")
    add_cmd.add_argument("--engine", required=True, choices=["sqlite", "mysql", "postgres"])
    add_cmd.add_argument("--database", required=True, help="SQLite 文件路径或数据库名")
    add_cmd.add_argument("--host", help="MySQL/PostgreSQL 主机地址")
    add_cmd.add_argument("--port", type=int, help="MySQL/PostgreSQL 端口")
    add_cmd.add_argument("--user", help="MySQL/PostgreSQL 用户名")
    add_cmd.add_argument("--password", help="数据库密码（不建议明文历史，优先 --password-stdin）")
    add_cmd.add_argument("--password-stdin", action="store_true", help="从 stdin 读取数据库密码")
    add_cmd.add_argument("--params-json", help="扩展配置 JSON 对象")
    add_cmd.add_argument("--set-default", action="store_true", help="设为默认连接")

    list_cmd = subparsers.add_parser("list", help="列出所有连接配置")
    list_cmd.add_argument("--json", action="store_true", help="以 JSON 输出")

    show_cmd = subparsers.add_parser("show", help="查看单个连接配置")
    show_cmd.add_argument("--name", required=True)

    update_cmd = subparsers.add_parser("update", help="更新连接配置")
    update_cmd.add_argument("--name", required=True)
    update_cmd.add_argument("--database", help="新数据库名/SQLite 路径")
    update_cmd.add_argument("--host", help="新主机地址")
    update_cmd.add_argument("--port", type=int, help="新端口")
    update_cmd.add_argument("--user", help="新用户名")
    update_cmd.add_argument("--password", help="新密码（优先 --password-stdin）")
    update_cmd.add_argument("--password-stdin", action="store_true", help="从 stdin 读取新密码")
    update_cmd.add_argument("--clear-password", action="store_true", help="清除已存储密码")
    update_cmd.add_argument("--params-json", help="新扩展配置 JSON 对象")
    update_cmd.add_argument("--enable", action="store_true", help="启用连接")
    update_cmd.add_argument("--disable", action="store_true", help="禁用连接")
    update_cmd.add_argument("--set-default", action="store_true", help="设为默认连接")

    remove_cmd = subparsers.add_parser("remove", help="删除连接配置")
    remove_cmd.add_argument("--name", required=True)

    default_cmd = subparsers.add_parser("set-default", help="设置默认连接")
    default_cmd.add_argument("--name", required=True)

    test_cmd = subparsers.add_parser("test", help="测试连接信息是否可读取（不执行写操作）")
    test_cmd.add_argument("--name", help="连接名，不传则使用默认连接")

    return parser.parse_args()


def read_password(args: argparse.Namespace) -> Optional[str]:
    if getattr(args, "password_stdin", False):
        # 允许把密码从 stdin 管道传入，目的是减少 shell 历史里的明文泄漏风险。
        return sys.stdin.read().rstrip("\n")
    return getattr(args, "password", None)


def print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    args = parse_args()
    registry_path = resolve_registry_path(args.registry)
    init_registry(registry_path)

    try:
        if args.command == "add":
            password = read_password(args)
            conn = add_connection(
                registry_path,
                name=args.name,
                engine=args.engine,
                database_name=args.database,
                host=args.host,
                port=args.port,
                username=args.user,
                password=password,
                params_json=args.params_json,
                set_default=args.set_default,
            )
            print_json({"ok": True, "action": "add", "registry": str(registry_path), "connection": conn})
            return 0

        if args.command == "list":
            conn_list = list_connections(registry_path)
            if args.json:
                print_json({"ok": True, "count": len(conn_list), "connections": conn_list})
            else:
                if not conn_list:
                    print("暂无连接配置")
                else:
                    for item in conn_list:
                        default_mark = " [default]" if item["is_default"] else ""
                        enabled_mark = "" if item["enabled"] else " [disabled]"
                        print(
                            f"- {item['name']} ({item['engine']}) {item['database_name']}"
                            f"{default_mark}{enabled_mark}"
                        )
            return 0

        if args.command == "show":
            conn = get_connection(registry_path, name=args.name)
            if conn is None:
                raise ValueError(f"连接不存在: {args.name}")
            print_json({"ok": True, "connection": conn})
            return 0

        if args.command == "update":
            if args.enable and args.disable:
                raise ValueError("--enable 与 --disable 不能同时使用")
            password = read_password(args)
            enabled = None
            if args.enable:
                enabled = True
            if args.disable:
                enabled = False
            conn = update_connection(
                registry_path,
                name=args.name,
                database_name=args.database,
                host=args.host,
                port=args.port,
                username=args.user,
                enabled=enabled,
                params_json=args.params_json,
                set_default=args.set_default,
                password=password,
                clear_password=args.clear_password,
            )
            print_json({"ok": True, "action": "update", "connection": conn})
            return 0

        if args.command == "remove":
            remove_connection(registry_path, name=args.name)
            print_json({"ok": True, "action": "remove", "name": args.name})
            return 0

        if args.command == "set-default":
            conn = set_default_connection(registry_path, name=args.name)
            print_json({"ok": True, "action": "set-default", "connection": conn})
            return 0

        if args.command == "test":
            runtime = get_connection_runtime_config(registry_path, name=args.name)
            # 这里只测试“配置可读取且密码可解密”，避免注册表层直接承担 SQL 执行职责。
            print_json(
                {
                    "ok": True,
                    "action": "test",
                    "connection_name": runtime["name"],
                    "engine": runtime["engine"],
                    "database": runtime["database"],
                    "has_password": runtime["password"] is not None,
                }
            )
            return 0

        raise ValueError(f"不支持的命令: {args.command}")
    except Exception as exc:
        print_json({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
