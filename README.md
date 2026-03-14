# db-crud-guard

一个面向 AI Agent 的数据库 CRUD 技能，提供统一 SQL 执行入口与默认安全护栏。  
支持 `SQLite / MySQL / PostgreSQL`，用于安全执行 `SELECT / INSERT / UPDATE / DELETE / REPLACE`。

## 1. 项目目标

`db-crud-guard` 解决两个核心问题：

1. 不同数据库重复写连接与执行代码，成本高且容易不一致。
2. 直接执行写 SQL 风险高，容易出现误改、误删、全表写入。

本项目通过统一脚本 + 强制安全策略，降低误操作风险，并便于在不同 Agent 平台复用。

## 2. 核心能力

- 统一执行脚本：`scripts/run_sql.py`
- 支持数据库：`sqlite`、`mysql`、`postgres`
- 支持语句：`SELECT/INSERT/UPDATE/DELETE/REPLACE`
- 默认只读：写操作需要显式确认
- 默认拦截无 `WHERE` 的 `UPDATE/DELETE`
- 默认拦截多语句执行
- 默认拒绝非 CRUD 语句（如 `DROP/ALTER/TRUNCATE`）
- 写入失败自动回滚
- 输出统一 JSON，方便自动化处理

## 3. 目录结构

```text
db-crud-guard/
├── SKILL.md                                  # 通用技能说明
├── scripts/
│   └── run_sql.py                            # 统一 SQL 执行脚本
├── references/
│   └── security-checklist.md                 # 执行前后核对清单
├── .agent/skills/db-crud-guard/SKILL.md      # Antigravity 入口
└── .claude/skills/db-crud-guard/SKILL.md     # Claude Code 入口
```

## 4. 安装方式

### 4.1 克隆仓库

```bash
git clone https://github.com/ZyaireXv/db-crud-guard.git
cd db-crud-guard
```

### 4.2 Python 依赖

基础环境：`Python 3.10+`

按数据库驱动安装：

```bash
# MySQL
python3 -m pip install --user pymysql

# PostgreSQL（二选一）
python3 -m pip install --user psycopg
# 或
python3 -m pip install --user psycopg2
```

`SQLite` 使用 Python 内置 `sqlite3`，无需额外安装。

## 5. 在不同平台启用技能

### 5.1 Antigravity

将技能目录放在工作区：

```text
<workspace-root>/.agent/skills/db-crud-guard/SKILL.md
```

请按安装文档迁移 `scripts/` 和 `references/` 到技能目录：
- `ANTIGRAVITY_INSTALL.md`

### 5.2 Claude Code（cc）

将技能目录放在工作区：

```text
<workspace-root>/.claude/skills/db-crud-guard/SKILL.md
```

请按安装文档迁移 `scripts/` 和 `references/` 到技能目录：
- `CLAUDE_INSTALL.md`

## 6. 快速使用

### 6.1 查询（只读）

```bash
python3 scripts/run_sql.py \
  --engine mysql \
  --host 127.0.0.1 \
  --port 3306 \
  --user app \
  --password '***' \
  --database entropat \
  --sql "SELECT id, nickname FROM member_user WHERE id = %s" \
  --params-json "[1001]"
```

### 6.2 写入（受控）

```bash
python3 scripts/run_sql.py \
  --engine mysql \
  --host 127.0.0.1 \
  --port 3306 \
  --user app \
  --password '***' \
  --database entropat \
  --sql "UPDATE member_user SET nickname = %s WHERE id = %s" \
  --params-json "[\"新昵称\", 1001]" \
  --allow-write \
  --confirm CONFIRM_WRITE
```

## 7. 安全策略说明

- 写操作必须同时带：
  - `--allow-write`
  - `--confirm CONFIRM_WRITE`
- `UPDATE/DELETE` 默认要求 `WHERE`
- 仅允许单条语句
- 仅允许 CRUD 语句
- 出错自动回滚
- 建议执行流程：先 `SELECT` 验证范围，再执行写入

## 8. 输出格式

脚本始终输出 JSON。

成功示例：

```json
{
  "ok": true,
  "engine": "mysql",
  "statement_type": "select",
  "is_write": false,
  "affected_rows": 0,
  "returned_rows": 1,
  "rows": [{"id": 1001, "nickname": "test"}]
}
```

失败示例：

```json
{
  "ok": false,
  "statement_type": "update",
  "is_write": true,
  "error": "UPDATE/DELETE 未检测到 WHERE，已拦截。若确需全表操作，请显式传 --allow-full-table-write"
}
```

## 9. 最佳实践

- 生产环境使用最小权限账号，避免使用 `root`
- 参数化 SQL，避免字符串拼接
- 保留执行记录（SQL、时间、影响行数）
- 高风险变更提前准备回滚方案
