---
name: db-crud-guard
description: 直连 SQLite、MySQL、PostgreSQL 数据库并执行 SQL 增删改查（CRUD），默认只读且对写操作启用双重确认、全表写保护和批量写入保护。用户要求直接查询数据、修复脏数据、批量更新记录或删除指定数据时使用；当任务明确需要执行 SQL 而不是改应用代码时触发。
---

# DB CRUD Guard

## 执行流程

1. 先收集连接信息（引擎、地址、库名、账号）并确认目标环境不是误连生产库。
2. 先执行 `SELECT` 验证命中范围，再决定是否执行写操作。
3. 通过脚本执行 SQL：
   `python3 scripts/run_sql.py ...`
4. 写操作必须同时传 `--allow-write --confirm CONFIRM_WRITE`，否则脚本会拒绝执行。
5. `UPDATE/DELETE` 默认要求有效 `WHERE` 条件；像 `WHERE 1=1`、`WHERE TRUE` 这类纯恒真条件也会被拦截。
6. `INSERT ... SELECT / REPLACE ... SELECT` 默认视为批量写入；如果确实要执行，再显式传 `--allow-bulk-write`。

## 自然语言触发示例

安装好 skill 后，用户不一定会直接说命令，更常见的是直接描述目标。下面这些表达都应该触发这个 skill：

- `为 db-crud-guard 添加一个 mysql 连接，ip 是 10.1.1.1，默认端口，用户名 user，密码 user。`
- `帮我对比一下表1和表2 的区别。`
- `分析一下 member_user 表结构有哪些缺点。`
- `查看 ai_chat_conversation 表结构，并给出索引设计建议。`

处理这类请求时，优先帮用户把“连接配置、结构查询、差异分析、风险提示”串成一个完整流程，不要只停留在给命令。

## 连接持久化

如果是长期使用同一批数据库，先录入连接，再按连接名执行，避免重复传敏感信息：

```bash
python3 scripts/db_registry.py add \
  --name test-mysql \
  --engine mysql \
  --host 127.0.0.1 \
  --port 3306 \
  --user app \
  --database entropat \
  --password-stdin \
  --set-default
```

```bash
python3 scripts/run_sql.py \
  --conn test-mysql \
  --sql "SELECT 1"
```

## 快速命令

参数占位符统一约定：

- 位置参数统一写 `%s`
- 命名参数统一写 `%(name)s`

SQLite 会在执行前自动转换成自身支持的 `?` / `:name`，所以不用为不同数据库改 SQL。

### SQLite 查询
```bash
python3 scripts/run_sql.py \
  --engine sqlite \
  --database ./tmp/test.db \
  --sql "SELECT id, name FROM user WHERE id = %(user_id)s" \
  --params-json '{"user_id": 1001}'
```

### MySQL 更新
```bash
python3 scripts/run_sql.py \
  --engine mysql \
  --host 127.0.0.1 \
  --port 3306 \
  --user root \
  --password '***' \
  --database entropat \
  --sql "UPDATE member_user SET nickname = %s WHERE id = %s" \
  --params-json '["新昵称", 1001]' \
  --allow-write \
  --confirm CONFIRM_WRITE
```

### PostgreSQL 删除
```bash
python3 scripts/run_sql.py \
  --engine postgres \
  --host 127.0.0.1 \
  --port 5432 \
  --user app \
  --password '***' \
  --database entropat \
  --sql "DELETE FROM audit_log WHERE created_at < %s" \
  --params-json '["2024-01-01"]' \
  --allow-write \
  --confirm CONFIRM_WRITE
```

## 关键约束

1. 一次只执行一条 SQL，多语句会被拦截，避免把排查脚本误当批处理跑进库里。
2. 写操作默认禁用，必须显式放行，避免误触发数据变更。
3. `UPDATE/DELETE` 无 `WHERE` 或纯恒真 `WHERE` 会被拦截，避免全表误改或误删。
4. `INSERT ... SELECT / REPLACE ... SELECT` 默认需要额外传 `--allow-bulk-write`，防止把批量导入当普通写入误跑。
5. 脚本失败时会回滚写事务，防止半成功状态污染数据。
6. 非 CRUD 语句（如 `DROP/ALTER/TRUNCATE`）会被拒绝，避免越权执行结构变更。

## 环境建议

1. 推荐优先用于开发环境、测试环境、预发布环境。
2. 如果要连生产库，优先使用最小权限账号，默认按只读思路工作。
3. 即使用户直接给了连接信息，也要先提醒环境风险，避免把“能连上”误解成“适合直接写入”。

完整安全建议见 [references/security-checklist.md](references/security-checklist.md)。

## 资源说明

- `scripts/run_sql.py`：统一 SQL 执行入口（SQLite/MySQL/PostgreSQL）
- `scripts/db_registry.py`：连接注册表管理（增删改查、默认连接）
- `scripts/registry_store.py`：注册表与密码存储公共逻辑
- `references/security-checklist.md`：执行前后核对清单

## 依赖说明

- SQLite：Python 内置 `sqlite3`，无需额外安装。
- MySQL：需要安装 `pymysql`（`python3 -m pip install --user pymysql`）。
- PostgreSQL：需要安装 `psycopg` 或 `psycopg2`（二选一）。
- 连接注册表密码存储：需要安装 `keyring`（`python3 -m pip install --user keyring`）。
