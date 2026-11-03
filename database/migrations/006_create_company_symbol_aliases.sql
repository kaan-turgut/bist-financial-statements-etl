CREATE TABLE IF NOT EXISTS metadata.company_symbol_aliases (
    company_id BIGINT NOT NULL,
    alias_symbol TEXT NOT NULL,
    PRIMARY KEY (company_id, alias_symbol),
    UNIQUE (alias_symbol),
    FOREIGN KEY (company_id)
        REFERENCES metadata.companies(company_id)
        ON DELETE CASCADE
);
