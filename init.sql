CREATE DATABASE IF NOT EXISTS recruitment_system
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE recruitment_system;

DROP TABLE IF EXISTS jobs;
CREATE TABLE jobs (
  id INT PRIMARY KEY AUTO_INCREMENT,
  title VARCHAR(100) NOT NULL,
  company VARCHAR(100) NOT NULL,
  salary VARCHAR(50) NOT NULL,
  salary_min DECIMAL(10,2) NOT NULL,
  salary_max DECIMAL(10,2) NOT NULL,
  salary_avg DECIMAL(10,2) NOT NULL,
  location VARCHAR(50) NOT NULL,
  experience VARCHAR(50) NOT NULL,
  education VARCHAR(50) NOT NULL,
  industry VARCHAR(50) NOT NULL,
  job_type VARCHAR(50) NOT NULL,
  company_nature VARCHAR(50) NOT NULL,
  company_size VARCHAR(50) NOT NULL,
  job_url VARCHAR(255) NOT NULL,
  skills TEXT NOT NULL,
  source VARCHAR(50) NOT NULL,
  company_logo VARCHAR(255) NOT NULL,
  crawl_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX uq_jobs_job_url ON jobs (job_url);
CREATE INDEX idx_jobs_title ON jobs (title);
CREATE INDEX idx_jobs_company ON jobs (company);
CREATE INDEX idx_jobs_salary_min ON jobs (salary_min);
CREATE INDEX idx_jobs_salary_max ON jobs (salary_max);
CREATE INDEX idx_jobs_location ON jobs (location);
CREATE INDEX idx_jobs_experience ON jobs (experience);
CREATE INDEX idx_jobs_education ON jobs (education);
CREATE INDEX idx_jobs_industry ON jobs (industry);
CREATE INDEX idx_jobs_source ON jobs (source);
CREATE INDEX idx_jobs_status ON jobs (status);
