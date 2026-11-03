CREATE TABLE IF NOT EXISTS metadata.company_indexes (
    company_id BIGINT NOT NULL,
    index_name TEXT NOT NULL,

    PRIMARY KEY (company_id, index_name),

    FOREIGN KEY (company_id)
        REFERENCES metadata.companies(company_id)
        ON DELETE CASCADE
);
