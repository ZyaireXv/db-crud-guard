# Claude Code 安装指南（.claude 目录版）

本文档用于让 Claude Code Agent 在目标项目中安装 `db-crud-guard`，并把技能做成“目录内自包含”（`SKILL.md + scripts + references`），避免跨目录引用。

## 1. 目标结构

安装完成后，目标项目应包含：

```text
<workspace-root>/
└── .claude/
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
mkdir -p .claude/skills/db-crud-guard

# 3) 复制技能入口
cp /tmp/db-crud-guard/.claude/skills/db-crud-guard/SKILL.md \
   .claude/skills/db-crud-guard/SKILL.md

# 4) 迁移脚本与参考资料到技能目录（关键步骤）
cp -R /tmp/db-crud-guard/scripts .claude/skills/db-crud-guard/scripts
cp -R /tmp/db-crud-guard/references .claude/skills/db-crud-guard/references

# 5) 清理字节码缓存（可选）
find .claude/skills/db-crud-guard/scripts -name "__pycache__" -type d -prune -exec rm -rf {} +
find .claude/skills/db-crud-guard/scripts -name "*.pyc" -delete
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
python3 .claude/skills/db-crud-guard/scripts/run_sql.py \
  --engine sqlite \
  --database /tmp/db-crud-guard-test.db \
  --sql "SELECT 1"
```

期望返回 `{"ok": true, ...}` 的 JSON。

## 5. 关键约定

Claude 技能内部统一使用：

```bash
python3 "$CLAUDE_SKILL_DIR/scripts/run_sql.py" ...
```

因此安装时必须把 `scripts/` 和 `references/` 一起迁移到技能目录内，不能只复制 `SKILL.md`。
