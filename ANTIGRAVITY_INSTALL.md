# Antigravity 安装指南（.agent 目录版）

本文档用于让 Antigravity Agent 在目标项目中安装 `db-crud-guard`，并把技能做成“目录内自包含”（`SKILL.md + scripts + references`），避免跨目录引用造成维护复杂度。

## 1. 目标结构

安装完成后，目标项目应包含：

```text
<workspace-root>/
└── .agent/
    └── skills/
        └── db-crud-guard/
            ├── SKILL.md
            ├── scripts/
            │   └── run_sql.py
            └── references/
                └── security-checklist.md
```

## 2. 安装步骤

在目标项目根目录执行：

```bash
# 1) 克隆技能仓库（建议放到临时目录）
git clone https://github.com/ZyaireXv/db-crud-guard.git /tmp/db-crud-guard

# 2) 创建技能目录
mkdir -p .agent/skills/db-crud-guard

# 3) 复制技能入口
cp /tmp/db-crud-guard/.agent/skills/db-crud-guard/SKILL.md \
   .agent/skills/db-crud-guard/SKILL.md

# 4) 迁移脚本与参考资料到技能目录（关键步骤）
cp -R /tmp/db-crud-guard/scripts .agent/skills/db-crud-guard/scripts
cp -R /tmp/db-crud-guard/references .agent/skills/db-crud-guard/references

# 5) 清理字节码缓存（可选）
find .agent/skills/db-crud-guard/scripts -name "__pycache__" -type d -prune -exec rm -rf {} +
find .agent/skills/db-crud-guard/scripts -name "*.pyc" -delete
```

## 3. 依赖安装

```bash
# MySQL 驱动
python3 -m pip install --user pymysql

# PostgreSQL 驱动（二选一）
python3 -m pip install --user psycopg
# 或
python3 -m pip install --user psycopg2
```

`SQLite` 无需额外依赖。

## 4. 快速验证

```bash
python3 .agent/skills/db-crud-guard/scripts/run_sql.py \
  --engine sqlite \
  --database /tmp/db-crud-guard-test.db \
  --sql "SELECT 1"
```

期望返回 `{"ok": true, ...}` 的 JSON。

## 5. 说明

1. 本安装流程固定使用 `.agent/skills`（单数目录）。
2. 如果你的 Antigravity 环境是 `.agents/skills`，只需把上述路径整体替换为 `.agents/skills` 即可。
