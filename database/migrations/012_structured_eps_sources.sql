ALTER TABLE bronze.eps_sources DROP CONSTRAINT eps_sources_source_kind_check;
ALTER TABLE bronze.eps_sources ADD CONSTRAINT eps_sources_source_kind_check
 CHECK (source_kind IN ('pdf','json','kap_html'));
