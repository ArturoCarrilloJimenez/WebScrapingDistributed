-- ==============================================================================
-- WebScrapingDistributed - Esquema de Base de Datos PostgreSQL
-- Gobernanza de Dominios, Compliance SEC y Politicas de Scraping
-- ==============================================================================

CREATE TABLE IF NOT EXISTS domain_policies (
    domain VARCHAR(255) PRIMARY KEY,
    hard_rate_limit DOUBLE PRECISION NULL,
    default_rate_limit DOUBLE PRECISION NULL,
    default_use_proxy BOOLEAN NOT NULL DEFAULT TRUE,
    default_respect_robots_txt BOOLEAN NOT NULL DEFAULT TRUE,
    default_headers JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indices para busquedas rapidas
CREATE INDEX IF NOT EXISTS idx_domain_policies_domain ON domain_policies (domain);

-- Semillas iniciales (Seed Data)
INSERT INTO domain_policies (
    domain,
    hard_rate_limit,
    default_rate_limit,
    default_use_proxy,
    default_respect_robots_txt,
    default_headers,
    created_at,
    updated_at
) VALUES 
(
    'sec.gov',
    10.0,
    10.0,
    FALSE,
    TRUE,
    '{"User-Agent": "Sample Company AdminContact@<sample company domain>.com", "Accept-Encoding": "gzip, deflate"}'::jsonb,
    NOW(),
    NOW()
),
(
    'data.sec.gov',
    10.0,
    10.0,
    FALSE,
    TRUE,
    '{"User-Agent": "Sample Company AdminContact@<sample company domain>.com", "Accept-Encoding": "gzip, deflate"}'::jsonb,
    NOW(),
    NOW()
),
(
    'default',
    NULL,
    NULL,
    TRUE,
    TRUE,
    NULL,
    NOW(),
    NOW()
)
ON CONFLICT (domain) DO NOTHING;
