# JobSpider

用于抓取招聘网站职位数据并写入 MySQL 的脚本项目。

当前主要脚本是：
- `job_zhilian.py`：智联招聘抓取（推荐使用）
- `job.py`：猎聘抓取（保留）
- `backfill_skills.py`：按已有 `job_url` 回填 `skills`（猎聘）

## 1. 环境要求

- Python 3.10+
- Chrome 浏览器
- MySQL 8.0（可通过 Docker 启动）

## 2. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 3. 启动数据库

```powershell
docker compose up -d
```

默认数据库连接（脚本内置）：
- host: `127.0.0.1`
- port: `3306`
- user: `root`
- password: `root`
- database: `recruitment_system`

## 4. 智联抓取（主流程）

### 4.1 关键说明

- 使用脚本：`job_zhilian.py`
- 抓取来源：智联搜索页（支持分页）
- `skills` 字段策略：
  1. 优先从职位数据/详情描述中提取
  2. 若抓不到，则从 `skills/` 目录下对应 JSON 随机取 4 条兜底

### 4.2 指纹文件

支持两种格式：
- `1.txt`（`user-agent/cookie/x-xsrf-token`）
- `任务流程.md` 中的请求头文本块（脚本会自动提取 `cookie` 和 `user-agent`）

### 4.3 运行示例

抓取 Python 岗位 2 页：

```powershell
.\.venv\Scripts\python job_zhilian.py --pages 2 --key python --base-url "https://www.zhaopin.com/sou/jl538/kwpython/p1?srccode=401801" --use-fingerprint --fingerprint-file "任务流程.md"
```

抓取 Go 岗位 3 页：

```powershell
.\.venv\Scripts\python job_zhilian.py --pages 3 --key go --base-url "https://www.zhaopin.com/sou/jl538/kwgo/p1?srccode=401801" --use-fingerprint --fingerprint-file "任务流程.md"
```

常用参数：
- `--pages`：抓取页数
- `--key`：关键词（用于兜底 skills 匹配）
- `--base-url`：智联搜索页 URL（带 `/p1`）
- `--use-fingerprint`：启用指纹
- `--fingerprint-file`：指纹文件路径
- `--cookie` / `--user-agent`：可直接命令行覆盖
- `--headless`：无头模式（有时更易触发风控，不建议）

## 5. 猎聘抓取（保留脚本）

```powershell
.\.venv\Scripts\python job.py --key java --pages 1
```

## 6. skills 回填（猎聘）

当库里已有 `job_url`，希望补 `skills` 时可用：

```powershell
.\.venv\Scripts\python backfill_skills.py --limit 200 --headless
```

仅预览不写库：

```powershell
.\.venv\Scripts\python backfill_skills.py --limit 50 --dry-run --headless
```

## 7. 数据表字段

写入目标表：`jobs`

主要字段：
- `title`
- `company`
- `salary` / `salary_min` / `salary_max` / `salary_avg`
- `location` / `experience` / `education`
- `industry` / `job_type`
- `company_nature` / `company_size`
- `job_url`
- `skills`
- `source`
- `company_logo`
- `crawl_date`

## 8. 常见问题

### 8.1 运行后 `saved 0 records`

- 大概率是风控或指纹失效
- 处理建议：
  - 使用最新 cookie
  - 不要加 `--headless`
  - 缩小页数先测（如 `--pages 1`）

### 8.2 `No module named selenium`

未使用虚拟环境解释器，请用：

```powershell
.\.venv\Scripts\python ...
```

### 8.3 乱码问题

- 控制台显示乱码通常不影响入库
- 文件建议统一 UTF-8 编码
