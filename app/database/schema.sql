-- 企业风险情报日报系统 — SQLite  schema
-- 与 SQLAlchemy 模型对应，可用于手工初始化或审计

CREATE TABLE IF NOT EXISTS domain_whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain VARCHAR(255) NOT NULL UNIQUE,
    module_code VARCHAR(8),
    note TEXT,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS domain_blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain VARCHAR(255) NOT NULL UNIQUE,
    reason TEXT,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS search_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_code VARCHAR(8) NOT NULL,
    query_text TEXT NOT NULL,
    domains_whitelist TEXT,
    domains_blacklist TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    raw_response TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS report_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date DATE NOT NULL,
    module_code VARCHAR(8) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    started_at DATETIME,
    finished_at DATETIME,
    entry_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    UNIQUE(report_date, module_code)
);

CREATE TABLE IF NOT EXISTS daily_risk_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date DATE NOT NULL,
    module_code VARCHAR(8) NOT NULL,
    country_or_region VARCHAR(128),
    target_entity VARCHAR(256),
    title VARCHAR(512) NOT NULL,
    related_company VARCHAR(256),
    risk_category VARCHAR(128),
    risk_level VARCHAR(16) NOT NULL,
    summary TEXT NOT NULL,
    impact_analysis TEXT,
    source_url VARCHAR(1024),
    source_title VARCHAR(512),
    pillar_or_topic VARCHAR(128),
    structured_json TEXT,
    search_log_id INTEGER REFERENCES search_logs(id),
    published_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_entries_report_date ON daily_risk_entries(report_date);
CREATE INDEX IF NOT EXISTS idx_entries_module ON daily_risk_entries(module_code);
CREATE INDEX IF NOT EXISTS idx_search_logs_module ON search_logs(module_code);
CREATE INDEX IF NOT EXISTS idx_report_runs_date ON report_runs(report_date);

-- 切片 2：三页业务表
CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date DATE NOT NULL,
    module_code VARCHAR(8) NOT NULL,
    category_tag VARCHAR(128),
    country_or_region VARCHAR(128),
    target_entity VARCHAR(256),
    title VARCHAR(512) NOT NULL,
    related_company VARCHAR(256),
    risk_category VARCHAR(128),
    risk_level VARCHAR(16) NOT NULL,
    summary TEXT NOT NULL,
    impact_analysis TEXT,
    source_url VARCHAR(1024),
    source_title VARCHAR(512),
    structured_json TEXT,
    published_at DATETIME,
    legacy_entry_id INTEGER REFERENCES daily_risk_entries(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS target_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(256) NOT NULL UNIQUE,
    display_name VARCHAR(256),
    aliases TEXT,
    industry VARCHAR(128),
    region VARCHAR(128),
    monitor_status VARCHAR(32) NOT NULL DEFAULT 'active',
    credit_level VARCHAR(16) NOT NULL DEFAULT '正常',
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entity_risks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES target_entities(id),
    report_date DATE NOT NULL,
    title VARCHAR(512) NOT NULL,
    risk_category VARCHAR(128),
    risk_level VARCHAR(16) NOT NULL,
    summary TEXT NOT NULL,
    impact_analysis TEXT,
    source_url VARCHAR(1024),
    related_company VARCHAR(256),
    structured_json TEXT,
    legacy_entry_id INTEGER REFERENCES daily_risk_entries(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS credit_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES target_entities(id),
    previous_level VARCHAR(16) NOT NULL,
    new_level VARCHAR(16) NOT NULL,
    reason TEXT,
    trigger_risk_id INTEGER REFERENCES entity_risks(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS industry_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    industry_name VARCHAR(256) NOT NULL,
    company_name VARCHAR(256),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    report_html TEXT,
    report_json TEXT,
    chart_specs TEXT,
    error_message TEXT,
    legacy_report_id INTEGER REFERENCES industry_analysis_reports(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_news_report_date ON news_articles(report_date);
CREATE INDEX IF NOT EXISTS idx_news_module ON news_articles(module_code);
CREATE INDEX IF NOT EXISTS idx_entity_risks_entity ON entity_risks(entity_id);
CREATE INDEX IF NOT EXISTS idx_credit_updates_entity ON credit_updates(entity_id);
CREATE INDEX IF NOT EXISTS idx_industry_reports_name ON industry_reports(industry_name);
