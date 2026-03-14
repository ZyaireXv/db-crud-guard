# db-crud-guard

一个面向 AI Agent 的数据库 CRUD 技能，提供统一 SQL 执行入口与默认安全护栏。  
支持 `SQLite / MySQL / PostgreSQL`，用于安全执行 `SELECT / INSERT / UPDATE / DELETE / REPLACE`。

## 安装说明（直接发给 Agent）

直接将以下文本复制并发送给对应 Agent，即可按文档安装 skills：

### Antigravity

```text
参考 https://github.com/ZyaireXv/db-crud-guard/blob/main/ANTIGRAVITY_INSTALL.md 安装 skills。
```

### Claude Code

```text
参考 https://github.com/ZyaireXv/db-crud-guard/blob/main/CLAUDE_INSTALL.md 安装 skills。
```

### Codex

```text
参考 https://github.com/ZyaireXv/db-crud-guard/blob/main/CODEX_INSTALL.md 安装 skills。
```

## 1. 项目目标

`db-crud-guard` 解决两个核心问题：

1. 不同数据库重复写连接与执行代码，成本高且容易不一致。
2. 直接执行写 SQL 风险高，容易出现误改、误删、全表写入。

本项目通过统一脚本 + 强制安全策略，降低误操作风险，并便于在不同 Agent 平台复用。

## 2. 核心能力

- 统一执行脚本：`scripts/run_sql.py`
- 连接注册表管理：`scripts/db_registry.py`
- 支持数据库：`sqlite`、`mysql`、`postgres`
- 支持语句：`SELECT/INSERT/UPDATE/DELETE/REPLACE`
- 默认只读：写操作需要显式确认
- 默认拦截无 `WHERE` 或纯恒真 `WHERE` 的 `UPDATE/DELETE`
- 默认拦截 `INSERT ... SELECT / REPLACE ... SELECT` 这类批量写入
- 默认拦截多语句执行
- 默认拒绝非 CRUD 语句（如 `DROP/ALTER/TRUNCATE`）
- 写入失败自动回滚
- SQLite 自动兼容统一占位符写法
- 输出统一 JSON，方便自动化处理

## 3. 目录结构

```text
db-crud-guard/
├── SKILL.md                                  # 通用技能说明
├── scripts/
│   ├── run_sql.py                            # 统一 SQL 执行脚本
│   ├── db_registry.py                        # 连接注册表管理脚本
│   └── registry_store.py                     # 注册表与密码存储公共层
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

如果你希望把依赖声明交给项目自身管理，也可以直接使用可选依赖：

```bash
# 只安装连接注册表的安全存储能力
python3 -m pip install --user '.[registry]'

# 安装 MySQL 支持
python3 -m pip install --user '.[mysql]'

# 安装 PostgreSQL 支持（psycopg3）
python3 -m pip install --user '.[postgres]'
```

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

### 5.3 Codex

请按安装文档迁移 `scripts/` 和 `references/` 到技能目录：
- `CODEX_INSTALL.md`

## 自然语言使用（推荐）

安装完成后，正常使用时不需要记脚本命令，直接把数据库配置和目标告诉 agent 即可。

在 skill 已安装、Python 依赖已就绪的前提下，agent 会自动复用 `db-crud-guard` 的连接管理和 SQL 执行能力，不需要你手工拼命令。

例如你可以直接这样说：

```text
为 db-crud-guard 添加一个 mysql 连接，ip 是 10.1.1.1，默认端口，用户名 user，密码 user。
```

```text
帮我对比一下表1和表2 的区别。
```

```text
分析一下 member_user 表结构有哪些缺点。
```

```text
查看 ai_chat_conversation 表结构，并给出索引设计上有哪些问题。
```

收到这类自然语言请求后，agent 一般会按下面的顺序工作：

1. 先保存或读取数据库连接配置
2. 再查询表结构、索引、字段定义和约束
3. 最后输出对比结果、结构缺点和改进建议

这意味着你平时只需要告诉 agent：

- 连接到哪个数据库
- 想查什么
- 想比较什么
- 想分析什么

不需要自己记 `run_sql.py` 或 `db_registry.py` 的命令细节。

## 6. 快速使用

### 6.1 统一占位符约定

为了让同一条 SQL 能跨 `SQLite / MySQL / PostgreSQL` 复用，脚本统一约定：

- 位置参数使用 `%s`
- 命名参数使用 `%(name)s`

其中 SQLite 会在执行前自动转换为自身支持的 `?` 或 `:name`，所以调用方不需要为不同数据库改 SQL 写法。

### 6.2 查询（只读）

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

命名参数示例：

```bash
python3 scripts/run_sql.py \
  --engine sqlite \
  --database ./tmp/test.db \
  --sql "SELECT id, nickname FROM member_user WHERE id = %(user_id)s" \
  --params-json '{"user_id": 1001}'
```

### 6.3 写入（受控）

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

### 6.4 批量写入（额外确认）

`INSERT ... SELECT` 和 `REPLACE ... SELECT` 默认会被拦截，因为它们很容易一次性影响大量数据。

```bash
python3 scripts/run_sql.py \
  --engine mysql \
  --host 127.0.0.1 \
  --port 3306 \
  --user app \
  --password '***' \
  --database entropat \
  --sql "INSERT INTO member_user_archive(id, nickname) SELECT id, nickname FROM member_user WHERE deleted = %s" \
  --params-json "[1]" \
  --allow-write \
  --allow-bulk-write \
  --confirm CONFIRM_WRITE
```

## 7. 连接配置持久化（推荐）

首次录入连接（只需一次）：

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

上面的命令会从 stdin 读取密码，例如：

```bash
echo 'your-password' | python3 scripts/db_registry.py add \
  --name test-mysql \
  --engine mysql \
  --host 127.0.0.1 \
  --port 3306 \
  --user app \
  --database entropat \
  --password-stdin \
  --set-default
```

查看数据库列表：

```bash
python3 scripts/db_registry.py list
```

后续按连接名执行（不再重复传 IP/账号/密码）：

```bash
python3 scripts/run_sql.py \
  --conn test-mysql \
  --sql "SELECT id, nickname FROM member_user LIMIT 20"
```

也可以不传 `--conn`，直接使用默认连接：

```bash
python3 scripts/run_sql.py \
  --sql "SELECT 1"
```

## 8. 安全策略说明

- 写操作必须同时带：
  - `--allow-write`
  - `--confirm CONFIRM_WRITE`
- `UPDATE/DELETE` 默认要求有效 `WHERE`，`WHERE 1=1`、`WHERE TRUE` 这类纯恒真条件也会被拦截
- `INSERT ... SELECT / REPLACE ... SELECT` 默认要求额外传 `--allow-bulk-write`
- 仅允许单条语句
- 仅允许 CRUD 语句
- 出错自动回滚
- 建议执行流程：先 `SELECT` 验证范围，再执行写入
- 当前安全检查仍属于轻量语义检查，不是完整 SQL AST 解析；高风险变更仍建议先人工复核命中范围

## 9. 输出格式

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
  "error": "UPDATE/DELETE 的 WHERE 条件为恒真表达式，仍等价于全表写入，已拦截。若确需执行，请显式传 --allow-full-table-write"
}
```

## 10. 最佳实践

- 推荐优先在开发环境、测试环境、预发布环境使用
- 如果必须接生产库，建议只给最小权限账号，优先只读，不要直接使用 `root`
- 即使 agent 已具备数据库操作能力，仍建议先做只读核对，再执行受控写入
- 生产环境使用最小权限账号，避免使用 `root`
- 参数化 SQL，避免字符串拼接
- 保留执行记录（SQL、时间、影响行数）
- 高风险变更提前准备回滚方案

## 11. 测试与 CI

- 单元测试：`python3 -m unittest discover -s tests -p 'test_*.py' -v`
- 语法校验：`python3 -m py_compile scripts/run_sql.py scripts/db_registry.py scripts/registry_store.py`
- GitHub Actions：仓库已提供 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)，会在 `push` 和 `pull_request` 时自动执行校验

## 12. 许可证

本项目采用 `MIT` 许可证，详见 [LICENSE](LICENSE)。
