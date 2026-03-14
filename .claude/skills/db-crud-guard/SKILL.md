---
name: db-crud-guard
description: Safely execute database CRUD SQL for SQLite, MySQL, and PostgreSQL with write confirmation and full-table protection. Use when querying data, fixing dirty data, or performing controlled INSERT/UPDATE/DELETE operations.
---

# DB CRUD Guard

优先复用仓库已有执行脚本，不要重复实现数据库连接逻辑：

`python3 "$CLAUDE_SKILL_DIR/../../../scripts/run_sql.py" ...`

## 执行流程

1. 先确认目标环境和连接参数，避免误连生产库。
2. 先执行 `SELECT` 验证命中范围，再执行写操作。
3. 写操作必须携带 `--allow-write --confirm CONFIRM_WRITE`。
4. `UPDATE/DELETE` 默认要求 `WHERE`，否则会被拦截。
5. 读取 JSON 输出中的 `ok`、`affected_rows`、`rows`、`error` 字段判断结果。

## 常用命令

### 查询

```bash
python3 "$CLAUDE_SKILL_DIR/../../../scripts/run_sql.py" \
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
python3 "$CLAUDE_SKILL_DIR/../../../scripts/run_sql.py" \
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

## 安全约束

1. 只允许单条 SQL，多语句会被拦截。
2. 只支持 `SELECT/INSERT/UPDATE/DELETE/REPLACE`，拒绝 DDL 语句。
3. 写入失败自动回滚事务，防止半成功污染数据。
4. 更完整核对清单见：`$CLAUDE_SKILL_DIR/../../../references/security-checklist.md`
