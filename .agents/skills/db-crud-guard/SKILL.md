---
name: db-crud-guard
description: Safely execute database CRUD SQL for SQLite, MySQL, and PostgreSQL with write confirmation and full-table protection. Use when querying data, fixing dirty data, or performing controlled INSERT/UPDATE/DELETE operations.
---

# DB CRUD Guard

使用这个技能时，不要直接手写数据库连接代码，优先复用仓库已有脚本：

`python3 ../../../scripts/run_sql.py`

## 执行流程

1. 先确认目标环境和库连接信息，避免误连生产库。
2. 先执行 `SELECT` 验证命中范围，再执行写操作。
3. 写操作必须带 `--allow-write --confirm CONFIRM_WRITE`。
4. `UPDATE/DELETE` 默认必须带 `WHERE`，否则会被拦截。
5. 输出是 JSON，直接读取 `affected_rows`、`rows`、`error` 字段。

## 常用命令

### 查询

```bash
python3 ../../../scripts/run_sql.py \
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
python3 ../../../scripts/run_sql.py \
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

1. 只允许单条 SQL，自动拦截多语句执行。
2. 只支持 `SELECT/INSERT/UPDATE/DELETE/REPLACE`，拒绝 DDL（如 `DROP/ALTER`）。
3. 写入失败会回滚事务，避免半成功状态。
4. 执行前后核对清单见：`../../../references/security-checklist.md`
