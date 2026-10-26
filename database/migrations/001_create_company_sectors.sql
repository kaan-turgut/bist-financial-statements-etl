CREATE TABLE metadata.company_sectors (
    company_id BIGINT NOT NULL,
    sector_name TEXT NOT NULL,

    PRIMARY KEY (company_id, sector_name),

    FOREIGN KEY (company_id)
        REFERENCES metadata.companies(company_id)
        ON DELETE CASCADE
);
