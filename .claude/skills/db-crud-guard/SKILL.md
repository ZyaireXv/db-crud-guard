---
name: db-crud-guard
description: Safely execute database CRUD SQL for SQLite, MySQL, and PostgreSQL with write confirmation, full-table protection, and bulk-write protection. Use when querying data, fixing dirty data, or performing controlled INSERT/UPDATE/DELETE operations.
---

# DB CRUD Guard

优先复用仓库已有执行脚本，不要重复实现数据库连接逻辑：

`python3 "$CLAUDE_SKILL_DIR/scripts/run_sql.py" ...`

如果是长期任务，先录入连接再按名称执行：

```bash
echo 'your-password' | python3 "$CLAUDE_SKILL_DIR/scripts/db_registry.py" add \
  --name test-mysql \
  --engine mysql \
  --host 127.0.0.1 \
  --port 3306 \
  --user app \
  --database entropat \
  --password-stdin \
  --set-default
```

用户也可能直接用自然语言描述目标，不会先说命令，例如：

- `为 db-crud-guard 添加一个 mysql 连接，ip 是 10.1.1.1，默认端口，用户名 user，密码 user。`
- `帮我对比一下表1和表2 的区别。`
- `分析一下 member_user 表结构有哪些缺点。`

遇到这类请求时，应该直接进入连接录入、结构查询、差异分析的完整流程，而不是只回复脚本用法。

## 执行流程

1. 先确认目标环境和连接参数，避免误连生产库。
2. 先执行 `SELECT` 验证命中范围，再执行写操作。
3. 写操作必须携带 `--allow-write --confirm CONFIRM_WRITE`。
4. `UPDATE/DELETE` 默认要求有效 `WHERE`；像 `WHERE 1=1` 这类纯恒真条件也会被拦截。
5. `INSERT ... SELECT / REPLACE ... SELECT` 默认要求额外传 `--allow-bulk-write`。
6. 读取 JSON 输出中的 `ok`、`affected_rows`、`rows`、`error` 字段判断结果。

推荐优先在开发环境、测试环境、预发布环境使用；如果用户给的是生产库，先提醒最小权限和只读优先原则。

## 常用命令

统一占位符约定：

- 位置参数统一写 `%s`
- 命名参数统一写 `%(name)s`

SQLite 会在执行前自动转换成 `?` / `:name`，所以同一条 SQL 可以跨库复用。

### 查询

```bash
python3 "$CLAUDE_SKILL_DIR/scripts/run_sql.py" \
  --engine mysql \
  --host 127.0.0.1 \
  --port 3306 \
  --user app \
  --password '***' \
  --database entropat \
  --sql "SELECT id, nickname FROM member_user WHERE id = %s" \
  --params-json "[1001]"
```

### 受控更新

```bash
python3 "$CLAUDE_SKILL_DIR/scripts/run_sql.py" \
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

### 按持久化连接执行

```bash
python3 "$CLAUDE_SKILL_DIR/scripts/run_sql.py" \
  --conn test-mysql \
  --sql "SELECT id, nickname FROM member_user LIMIT 20"
```

## 安全约束

1. 只允许单条 SQL，多语句会被拦截。
2. 只支持 `SELECT/INSERT/UPDATE/DELETE/REPLACE`，拒绝 DDL 语句。
3. `UPDATE/DELETE` 的纯恒真 `WHERE` 会被拦截，避免表面有条件、实际仍是全表写入。
4. 批量写入默认需要 `--allow-bulk-write`。
5. 写入失败自动回滚事务，防止半成功污染数据。
6. 更完整核对清单见：`$CLAUDE_SKILL_DIR/references/security-checklist.md`
