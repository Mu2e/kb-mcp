-- Schema-only dump of the live kb-mcp database (mu2e_docdb_dev @ ifdb10.fnal.gov).
-- Generated 2026-08-27T17:19:05Z via: pg_dump --schema-only --no-owner --no-acl
-- A static snapshot, not auto-regenerated — may drift from the live schema over time.
-- Regenerate with: scripts/dump_db.sh (adjusted to --schema-only), or see docs/guides/database.md
-- for the model-derived (code-authoritative) documentation.

--
-- PostgreSQL database dump
--

\restrict yPxEO56EZG4561VYYIdfbnDW6RnzbkjbSuogu2b6aJgFTSH3Ahg8632zQtw697Z

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: update_chunk_text_search_vector(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_chunk_text_search_vector() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                doc_title TEXT;
                doc_summary TEXT;
            BEGIN
                SELECT
                    COALESCE(title, title_gen, ''),
                    COALESCE(summary, '')
                INTO doc_title, doc_summary
                FROM documents
                WHERE id = NEW.document_id;

                NEW.text_search_vector :=
                    setweight(to_tsvector('english', doc_title), 'A') ||
                    setweight(to_tsvector('english', COALESCE(NEW.text, '')), 'B') ||
                    setweight(to_tsvector('english', COALESCE(NEW.section_path, '')), 'C') ||
                    setweight(to_tsvector('english', doc_summary), 'D');

                RETURN NEW;
            END;
            $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: chunk_strategies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunk_strategies (
    strategy character varying(128) NOT NULL,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunks (
    id character varying(36) NOT NULL,
    document_id character varying(36) NOT NULL,
    text text NOT NULL,
    chunk_index integer NOT NULL,
    char_start_index integer,
    char_end_index integer,
    token_length integer,
    section_path text,
    chunk_strategy character varying(128),
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    text_search_vector tsvector
);


--
-- Name: document_parser_outputs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_parser_outputs (
    document_id character varying(36) NOT NULL,
    output jsonb NOT NULL,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id character varying(36) NOT NULL,
    source_id character varying(256) NOT NULL,
    raw_document_id character varying(36),
    parser_id character varying(128),
    doc_id character varying(512),
    uri character varying(2048),
    source_type character varying(128) NOT NULL,
    doc_type character varying(64) NOT NULL,
    text text,
    "binary" bytea,
    meta jsonb,
    creating_time timestamp with time zone,
    update_time timestamp with time zone,
    insert_time timestamp with time zone DEFAULT now() NOT NULL,
    parent_id character varying(36),
    title text,
    title_gen text,
    summary text,
    gist text,
    content_hash character varying(64)
);


--
-- Name: documents_raw; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents_raw (
    id character varying(36) NOT NULL,
    source_id character varying(256) NOT NULL,
    doc_id character varying(512),
    file_path character varying(2048),
    hostname character varying(256),
    uri character varying(2048),
    source_type character varying(128) NOT NULL,
    file_size integer,
    content_hash character varying(64) NOT NULL,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    updated_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: embedding_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.embedding_configs (
    short_name character varying(64) NOT NULL,
    provider character varying(64) NOT NULL,
    model character varying(128) NOT NULL,
    dimension integer NOT NULL,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: embeddings_st_bgesmallenv1_5; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.embeddings_st_bgesmallenv1_5 (
    id character varying(36) NOT NULL,
    chunk_id character varying(36) NOT NULL,
    embedding public.vector(384) NOT NULL,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: embeddings_st_minilml6v2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.embeddings_st_minilml6v2 (
    id character varying(36) NOT NULL,
    chunk_id character varying(36) NOT NULL,
    embedding public.vector(384) NOT NULL,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eval_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_audit (
    id character varying(36) NOT NULL,
    question_id character varying(36) NOT NULL,
    is_valid boolean NOT NULL,
    audit_type character varying(64) NOT NULL,
    comments text,
    auditor_name character varying(128),
    score double precision,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eval_dataset; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_dataset (
    id character varying(36) NOT NULL,
    question text NOT NULL,
    generation_id character varying(36),
    source_document_id character varying(36),
    answer text,
    keypoints text,
    generation_time_seconds double precision,
    hostname character varying(256),
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eval_generation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_generation (
    id character varying(36) NOT NULL,
    name character varying(256),
    generation_type character varying(32) NOT NULL,
    generation_method character varying(64),
    prompt text,
    source_id character varying(256),
    source_type character varying(64) NOT NULL,
    source_filters jsonb,
    meta jsonb,
    content_hash character varying(64),
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eval_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_results (
    id character varying(36) NOT NULL,
    run_id character varying(36) NOT NULL,
    question_id character varying(36) NOT NULL,
    is_hit boolean NOT NULL,
    hit_rank integer,
    is_judge_hit boolean,
    justification text,
    judge_time_seconds double precision,
    retrieval_time_seconds double precision,
    hostname character varying(256),
    best_similarity double precision,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eval_retrieved_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_retrieved_documents (
    id character varying(36) NOT NULL,
    result_id character varying(36) NOT NULL,
    document_id character varying(36),
    rank integer NOT NULL,
    similarity double precision,
    chunk_ids jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eval_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eval_runs (
    id character varying(36) NOT NULL,
    name character varying(256),
    description text,
    generation_id character varying(36),
    audit_filters jsonb,
    embedding_name character varying(64),
    chunking_strategy character varying(128),
    max_results integer NOT NULL,
    search_filters jsonb,
    judge_strategy jsonb,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    search_type character varying(32) DEFAULT 'semantic'::character varying
);


--
-- Name: graph_extraction_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.graph_extraction_logs (
    id character varying(36) NOT NULL,
    document_id character varying(36) NOT NULL,
    hostname character varying(256),
    extraction_model character varying(128) NOT NULL,
    time_extraction double precision,
    time_processing double precision,
    relations_extracted integer NOT NULL,
    relations_processed integer NOT NULL,
    relations_created integer NOT NULL,
    relations_updated integer NOT NULL,
    relations_errors integer NOT NULL,
    error_details jsonb,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: graph_node_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.graph_node_map (
    node_id character varying(36) NOT NULL,
    document_id character varying(36) NOT NULL,
    count integer NOT NULL,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    updated_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: graph_node_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.graph_node_types (
    id character varying(36) NOT NULL,
    label character varying(128) NOT NULL,
    description text,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: graph_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.graph_nodes (
    id character varying(36) NOT NULL,
    type_id character varying(36) NOT NULL,
    name character varying(512) NOT NULL,
    canonical_name character varying(512) NOT NULL,
    embedding public.vector,
    embedding_name character varying(64),
    meta jsonb,
    aliases character varying[],
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    updated_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: graph_relations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.graph_relations (
    id character varying(36) NOT NULL,
    source_id character varying(36) NOT NULL,
    verb_id character varying(36) NOT NULL,
    target_id character varying(36) NOT NULL,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    updated_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: graph_relations_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.graph_relations_evidence (
    id character varying(36) NOT NULL,
    relation_id character varying(36) NOT NULL,
    document_id character varying(36),
    evidence_text text,
    confidence double precision,
    extraction_model character varying(128),
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: graph_verbs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.graph_verbs (
    id character varying(36) NOT NULL,
    name character varying(128) NOT NULL,
    description text,
    inverse_verb_id character varying(36),
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: llm_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_usage (
    id character varying(36) NOT NULL,
    stage character varying(64) NOT NULL,
    model character varying(256),
    document_id character varying(36),
    raw_document_id character varying(36),
    prompt_tokens integer NOT NULL,
    completion_tokens integer NOT NULL,
    total_tokens integer NOT NULL,
    main_context_tokens integer NOT NULL,
    cached_prompt_tokens integer NOT NULL,
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb
);


--
-- Name: logs_chunk_embedding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.logs_chunk_embedding (
    id character varying(36) NOT NULL,
    document_id character varying(36) NOT NULL,
    chunking_time_seconds double precision NOT NULL,
    embedding_time_seconds double precision NOT NULL,
    total_time_seconds double precision NOT NULL,
    num_chunks integer NOT NULL,
    num_embeddings integer NOT NULL,
    chunk_strategy character varying(128),
    embedding_name character varying(64),
    hostname character varying(256),
    insertion_time timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb
);


--
-- Name: logs_parsing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.logs_parsing (
    id character varying(36) NOT NULL,
    document_id character varying(36),
    text_extraction_time_seconds double precision NOT NULL,
    image_description_time_seconds double precision,
    total_time_seconds double precision NOT NULL,
    num_documents integer NOT NULL,
    text_length integer,
    hostname character varying(256),
    insertion_time timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb
);


--
-- Name: logs_search; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.logs_search (
    id character varying(36) NOT NULL,
    search_type character varying(32) NOT NULL,
    query text NOT NULL,
    embedding_name character varying(128),
    max_results integer NOT NULL,
    source_id character varying(128),
    doc_type character varying(128),
    chunking_strategy character varying(128),
    filter_params jsonb,
    metadata_filters jsonb,
    results jsonb NOT NULL,
    best_similarity double precision,
    best_rank double precision,
    total_results integer NOT NULL,
    time_search_total double precision,
    time_embedding double precision,
    time_deduplication double precision,
    time_db_fetch double precision,
    time_distance_calc double precision,
    time_sort double precision,
    time_semantic double precision,
    time_fulltext double precision,
    time_fusion double precision,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: logs_summary; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.logs_summary (
    id character varying(36) NOT NULL,
    document_id character varying(36),
    model character varying(128) NOT NULL,
    time_summary double precision NOT NULL,
    hostname character varying(256),
    insertion_time timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb
);


--
-- Name: parser_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parser_categories (
    id character varying(36) NOT NULL,
    source_id character varying(256) NOT NULL,
    title text,
    categories_text text,
    model character varying(256),
    num_comparisons integer,
    comparison_ids jsonb NOT NULL,
    prompt text,
    prompt_extra text,
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb
);


--
-- Name: parser_comparisons; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parser_comparisons (
    id character varying(36) NOT NULL,
    raw_document_id character varying(36) NOT NULL,
    document_ids jsonb NOT NULL,
    parser_ids jsonb NOT NULL,
    document_description text,
    comparison text,
    categories jsonb,
    model character varying(256),
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb
);


--
-- Name: parsers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parsers (
    name character varying(128) NOT NULL,
    description text,
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: privacy_filters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.privacy_filters (
    id character varying(36) NOT NULL,
    raw_document_id character varying(36) NOT NULL,
    document_id character varying(36),
    label character varying(32) NOT NULL,
    reasoning text,
    model character varying(256),
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb
);


--
-- Name: sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sources (
    id character varying(256) NOT NULL,
    name character varying(512),
    description text,
    base_uri character varying(2048),
    meta jsonb,
    created_time timestamp with time zone DEFAULT now() NOT NULL,
    updated_time timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: chunk_strategies chunk_strategies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunk_strategies
    ADD CONSTRAINT chunk_strategies_pkey PRIMARY KEY (strategy);


--
-- Name: chunks chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (id);


--
-- Name: document_parser_outputs document_parser_outputs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_parser_outputs
    ADD CONSTRAINT document_parser_outputs_pkey PRIMARY KEY (document_id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: documents_raw documents_raw_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents_raw
    ADD CONSTRAINT documents_raw_pkey PRIMARY KEY (id);


--
-- Name: embedding_configs embedding_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embedding_configs
    ADD CONSTRAINT embedding_configs_pkey PRIMARY KEY (short_name);


--
-- Name: embeddings_st_bgesmallenv1_5 embeddings_st_bgesmallenv1_5_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings_st_bgesmallenv1_5
    ADD CONSTRAINT embeddings_st_bgesmallenv1_5_pkey PRIMARY KEY (id);


--
-- Name: embeddings_st_minilml6v2 embeddings_st_minilml6v2_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings_st_minilml6v2
    ADD CONSTRAINT embeddings_st_minilml6v2_pkey PRIMARY KEY (id);


--
-- Name: eval_audit eval_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_audit
    ADD CONSTRAINT eval_audit_pkey PRIMARY KEY (id);


--
-- Name: eval_dataset eval_dataset_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_dataset
    ADD CONSTRAINT eval_dataset_pkey PRIMARY KEY (id);


--
-- Name: eval_generation eval_generation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_generation
    ADD CONSTRAINT eval_generation_pkey PRIMARY KEY (id);


--
-- Name: eval_results eval_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_results
    ADD CONSTRAINT eval_results_pkey PRIMARY KEY (id);


--
-- Name: eval_retrieved_documents eval_retrieved_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_retrieved_documents
    ADD CONSTRAINT eval_retrieved_documents_pkey PRIMARY KEY (id);


--
-- Name: eval_runs eval_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_runs
    ADD CONSTRAINT eval_runs_pkey PRIMARY KEY (id);


--
-- Name: graph_extraction_logs graph_extraction_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_extraction_logs
    ADD CONSTRAINT graph_extraction_logs_pkey PRIMARY KEY (id);


--
-- Name: graph_node_map graph_node_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_node_map
    ADD CONSTRAINT graph_node_map_pkey PRIMARY KEY (node_id, document_id);


--
-- Name: graph_node_types graph_node_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_node_types
    ADD CONSTRAINT graph_node_types_pkey PRIMARY KEY (id);


--
-- Name: graph_nodes graph_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_nodes
    ADD CONSTRAINT graph_nodes_pkey PRIMARY KEY (id);


--
-- Name: graph_relations_evidence graph_relations_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_relations_evidence
    ADD CONSTRAINT graph_relations_evidence_pkey PRIMARY KEY (id);


--
-- Name: graph_relations graph_relations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_relations
    ADD CONSTRAINT graph_relations_pkey PRIMARY KEY (id);


--
-- Name: graph_verbs graph_verbs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_verbs
    ADD CONSTRAINT graph_verbs_pkey PRIMARY KEY (id);


--
-- Name: llm_usage llm_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage
    ADD CONSTRAINT llm_usage_pkey PRIMARY KEY (id);


--
-- Name: logs_chunk_embedding logs_chunk_embedding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.logs_chunk_embedding
    ADD CONSTRAINT logs_chunk_embedding_pkey PRIMARY KEY (id);


--
-- Name: logs_parsing logs_parsing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.logs_parsing
    ADD CONSTRAINT logs_parsing_pkey PRIMARY KEY (id);


--
-- Name: logs_search logs_search_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.logs_search
    ADD CONSTRAINT logs_search_pkey PRIMARY KEY (id);


--
-- Name: logs_summary logs_summary_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.logs_summary
    ADD CONSTRAINT logs_summary_pkey PRIMARY KEY (id);


--
-- Name: parser_categories parser_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parser_categories
    ADD CONSTRAINT parser_categories_pkey PRIMARY KEY (id);


--
-- Name: parser_comparisons parser_comparisons_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parser_comparisons
    ADD CONSTRAINT parser_comparisons_pkey PRIMARY KEY (id);


--
-- Name: parsers parsers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsers
    ADD CONSTRAINT parsers_pkey PRIMARY KEY (name);


--
-- Name: privacy_filters privacy_filters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.privacy_filters
    ADD CONSTRAINT privacy_filters_pkey PRIMARY KEY (id);


--
-- Name: sources sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_pkey PRIMARY KEY (id);


--
-- Name: embedding_configs uq_embedding_config_provider_model; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embedding_configs
    ADD CONSTRAINT uq_embedding_config_provider_model UNIQUE (provider, model);


--
-- Name: eval_results uq_eval_result_run_question; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_results
    ADD CONSTRAINT uq_eval_result_run_question UNIQUE (run_id, question_id);


--
-- Name: eval_retrieved_documents uq_eval_retrieved_doc_result_rank; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_retrieved_documents
    ADD CONSTRAINT uq_eval_retrieved_doc_result_rank UNIQUE (result_id, rank);


--
-- Name: embeddings_st_bgesmallenv1_5_vector_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX embeddings_st_bgesmallenv1_5_vector_idx ON public.embeddings_st_bgesmallenv1_5 USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='250');


--
-- Name: embeddings_st_minilml6v2_vector_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX embeddings_st_minilml6v2_vector_idx ON public.embeddings_st_minilml6v2 USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='82');


--
-- Name: idx_chunks_document_id_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_document_id_index ON public.chunks USING btree (document_id, chunk_index);


--
-- Name: idx_chunks_document_id_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_document_id_start ON public.chunks USING btree (document_id, char_start_index);


--
-- Name: idx_chunks_text_search_vector; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_text_search_vector ON public.chunks USING gin (text_search_vector);


--
-- Name: idx_documents_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_content_hash ON public.documents USING btree (content_hash);


--
-- Name: idx_documents_insert_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_insert_time ON public.documents USING btree (insert_time);


--
-- Name: idx_documents_raw_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_raw_content_hash ON public.documents_raw USING btree (content_hash);


--
-- Name: idx_documents_raw_source_doc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_raw_source_doc_id ON public.documents_raw USING btree (source_id, doc_id);


--
-- Name: idx_documents_source_doc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_source_doc_id ON public.documents USING btree (source_id, doc_id);


--
-- Name: idx_documents_source_doc_parser; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_source_doc_parser ON public.documents USING btree (source_id, doc_id, parser_id);


--
-- Name: idx_embedding_config_provider_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embedding_config_provider_model ON public.embedding_configs USING btree (provider, model);


--
-- Name: idx_embeddings_st_bgesmallenv1_5_chunk_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embeddings_st_bgesmallenv1_5_chunk_id ON public.embeddings_st_bgesmallenv1_5 USING btree (chunk_id);


--
-- Name: idx_embeddings_st_minilml6v2_chunk_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embeddings_st_minilml6v2_chunk_id ON public.embeddings_st_minilml6v2 USING btree (chunk_id);


--
-- Name: idx_eval_audit_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_audit_created_time ON public.eval_audit USING btree (created_time);


--
-- Name: idx_eval_audit_is_valid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_audit_is_valid ON public.eval_audit USING btree (is_valid);


--
-- Name: idx_eval_audit_question_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_audit_question_id ON public.eval_audit USING btree (question_id);


--
-- Name: idx_eval_audit_question_valid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_audit_question_valid ON public.eval_audit USING btree (question_id, is_valid);


--
-- Name: idx_eval_audit_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_audit_type ON public.eval_audit USING btree (audit_type);


--
-- Name: idx_eval_dataset_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_dataset_created_time ON public.eval_dataset USING btree (created_time);


--
-- Name: idx_eval_dataset_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_dataset_generation_id ON public.eval_dataset USING btree (generation_id);


--
-- Name: idx_eval_dataset_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_dataset_hostname ON public.eval_dataset USING btree (hostname);


--
-- Name: idx_eval_dataset_source_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_dataset_source_document_id ON public.eval_dataset USING btree (source_document_id);


--
-- Name: idx_eval_generation_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_generation_created_time ON public.eval_generation USING btree (created_time);


--
-- Name: idx_eval_generation_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_generation_method ON public.eval_generation USING btree (generation_method);


--
-- Name: idx_eval_generation_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_generation_source_id ON public.eval_generation USING btree (source_id);


--
-- Name: idx_eval_generation_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_generation_type ON public.eval_generation USING btree (generation_type);


--
-- Name: idx_eval_results_hit_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_results_hit_rank ON public.eval_results USING btree (hit_rank);


--
-- Name: idx_eval_results_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_results_hostname ON public.eval_results USING btree (hostname);


--
-- Name: idx_eval_results_is_hit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_results_is_hit ON public.eval_results USING btree (is_hit);


--
-- Name: idx_eval_results_is_judge_hit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_results_is_judge_hit ON public.eval_results USING btree (is_judge_hit);


--
-- Name: idx_eval_results_question_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_results_question_id ON public.eval_results USING btree (question_id);


--
-- Name: idx_eval_results_run_hit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_results_run_hit ON public.eval_results USING btree (run_id, is_hit);


--
-- Name: idx_eval_results_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_results_run_id ON public.eval_results USING btree (run_id);


--
-- Name: idx_eval_results_run_judge_hit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_results_run_judge_hit ON public.eval_results USING btree (run_id, is_judge_hit);


--
-- Name: idx_eval_retrieved_docs_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_retrieved_docs_document_id ON public.eval_retrieved_documents USING btree (document_id);


--
-- Name: idx_eval_retrieved_docs_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_retrieved_docs_rank ON public.eval_retrieved_documents USING btree (rank);


--
-- Name: idx_eval_retrieved_docs_result_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_retrieved_docs_result_id ON public.eval_retrieved_documents USING btree (result_id);


--
-- Name: idx_eval_runs_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_runs_created_time ON public.eval_runs USING btree (created_time);


--
-- Name: idx_eval_runs_embedding_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_runs_embedding_name ON public.eval_runs USING btree (embedding_name);


--
-- Name: idx_eval_runs_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eval_runs_generation_id ON public.eval_runs USING btree (generation_id);


--
-- Name: idx_graph_extraction_logs_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_extraction_logs_created ON public.graph_extraction_logs USING btree (created_time);


--
-- Name: idx_graph_extraction_logs_document; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_extraction_logs_document ON public.graph_extraction_logs USING btree (document_id);


--
-- Name: idx_graph_nodes_aliases; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_nodes_aliases ON public.graph_nodes USING gin (aliases);


--
-- Name: idx_graph_nodes_type_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_nodes_type_name ON public.graph_nodes USING btree (type_id, canonical_name);


--
-- Name: idx_graph_relations_source_verb; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_graph_relations_source_verb ON public.graph_relations USING btree (source_id, verb_id);


--
-- Name: idx_graph_relations_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_graph_relations_unique ON public.graph_relations USING btree (source_id, verb_id, target_id);


--
-- Name: idx_logs_chunk_embedding_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_chunk_embedding_document_id ON public.logs_chunk_embedding USING btree (document_id);


--
-- Name: idx_logs_chunk_embedding_embedding_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_chunk_embedding_embedding_name ON public.logs_chunk_embedding USING btree (embedding_name);


--
-- Name: idx_logs_chunk_embedding_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_chunk_embedding_hostname ON public.logs_chunk_embedding USING btree (hostname);


--
-- Name: idx_logs_chunk_embedding_insertion_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_chunk_embedding_insertion_time ON public.logs_chunk_embedding USING btree (insertion_time);


--
-- Name: idx_logs_chunk_embedding_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_chunk_embedding_strategy ON public.logs_chunk_embedding USING btree (chunk_strategy);


--
-- Name: idx_logs_parsing_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_parsing_document_id ON public.logs_parsing USING btree (document_id);


--
-- Name: idx_logs_parsing_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_parsing_hostname ON public.logs_parsing USING btree (hostname);


--
-- Name: idx_logs_parsing_insertion_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_parsing_insertion_time ON public.logs_parsing USING btree (insertion_time);


--
-- Name: idx_logs_summary_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_summary_document_id ON public.logs_summary USING btree (document_id);


--
-- Name: idx_logs_summary_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_summary_hostname ON public.logs_summary USING btree (hostname);


--
-- Name: idx_logs_summary_insertion_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_summary_insertion_time ON public.logs_summary USING btree (insertion_time);


--
-- Name: idx_logs_summary_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_logs_summary_model ON public.logs_summary USING btree (model);


--
-- Name: ix_chunk_strategies_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunk_strategies_created_time ON public.chunk_strategies USING btree (created_time);


--
-- Name: ix_chunks_char_start_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunks_char_start_index ON public.chunks USING btree (char_start_index);


--
-- Name: ix_chunks_chunk_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunks_chunk_index ON public.chunks USING btree (chunk_index);


--
-- Name: ix_chunks_chunk_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunks_chunk_strategy ON public.chunks USING btree (chunk_strategy);


--
-- Name: ix_chunks_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunks_created_time ON public.chunks USING btree (created_time);


--
-- Name: ix_chunks_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunks_document_id ON public.chunks USING btree (document_id);


--
-- Name: ix_documents_creating_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_creating_time ON public.documents USING btree (creating_time);


--
-- Name: ix_documents_doc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_doc_id ON public.documents USING btree (doc_id);


--
-- Name: ix_documents_doc_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_doc_type ON public.documents USING btree (doc_type);


--
-- Name: ix_documents_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_parent_id ON public.documents USING btree (parent_id);


--
-- Name: ix_documents_parser_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_parser_id ON public.documents USING btree (parser_id);


--
-- Name: ix_documents_raw_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_raw_content_hash ON public.documents_raw USING btree (content_hash);


--
-- Name: ix_documents_raw_doc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_raw_doc_id ON public.documents_raw USING btree (doc_id);


--
-- Name: ix_documents_raw_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_raw_document_id ON public.documents USING btree (raw_document_id);


--
-- Name: ix_documents_raw_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_raw_hostname ON public.documents_raw USING btree (hostname);


--
-- Name: ix_documents_raw_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_raw_id ON public.documents_raw USING btree (id);


--
-- Name: ix_documents_raw_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_raw_source_id ON public.documents_raw USING btree (source_id);


--
-- Name: ix_documents_raw_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_raw_source_type ON public.documents_raw USING btree (source_type);


--
-- Name: ix_documents_raw_uri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_raw_uri ON public.documents_raw USING btree (uri);


--
-- Name: ix_documents_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_source_id ON public.documents USING btree (source_id);


--
-- Name: ix_documents_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_source_type ON public.documents USING btree (source_type);


--
-- Name: ix_documents_update_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_update_time ON public.documents USING btree (update_time);


--
-- Name: ix_documents_uri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_documents_uri ON public.documents USING btree (uri);


--
-- Name: ix_embedding_configs_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_embedding_configs_created_time ON public.embedding_configs USING btree (created_time);


--
-- Name: ix_embedding_configs_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_embedding_configs_model ON public.embedding_configs USING btree (model);


--
-- Name: ix_embedding_configs_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_embedding_configs_provider ON public.embedding_configs USING btree (provider);


--
-- Name: ix_embeddings_st_bgesmallenv1_5_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_embeddings_st_bgesmallenv1_5_created_time ON public.embeddings_st_bgesmallenv1_5 USING btree (created_time);


--
-- Name: ix_embeddings_st_minilml6v2_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_embeddings_st_minilml6v2_created_time ON public.embeddings_st_minilml6v2 USING btree (created_time);


--
-- Name: ix_eval_audit_audit_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_audit_audit_type ON public.eval_audit USING btree (audit_type);


--
-- Name: ix_eval_audit_auditor_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_audit_auditor_name ON public.eval_audit USING btree (auditor_name);


--
-- Name: ix_eval_audit_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_audit_created_time ON public.eval_audit USING btree (created_time);


--
-- Name: ix_eval_audit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_audit_id ON public.eval_audit USING btree (id);


--
-- Name: ix_eval_audit_is_valid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_audit_is_valid ON public.eval_audit USING btree (is_valid);


--
-- Name: ix_eval_audit_question_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_audit_question_id ON public.eval_audit USING btree (question_id);


--
-- Name: ix_eval_dataset_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_dataset_created_time ON public.eval_dataset USING btree (created_time);


--
-- Name: ix_eval_dataset_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_dataset_generation_id ON public.eval_dataset USING btree (generation_id);


--
-- Name: ix_eval_dataset_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_dataset_hostname ON public.eval_dataset USING btree (hostname);


--
-- Name: ix_eval_dataset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_dataset_id ON public.eval_dataset USING btree (id);


--
-- Name: ix_eval_dataset_source_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_dataset_source_document_id ON public.eval_dataset USING btree (source_document_id);


--
-- Name: ix_eval_generation_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_eval_generation_content_hash ON public.eval_generation USING btree (content_hash);


--
-- Name: ix_eval_generation_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_generation_created_time ON public.eval_generation USING btree (created_time);


--
-- Name: ix_eval_generation_generation_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_generation_generation_method ON public.eval_generation USING btree (generation_method);


--
-- Name: ix_eval_generation_generation_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_generation_generation_type ON public.eval_generation USING btree (generation_type);


--
-- Name: ix_eval_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_generation_id ON public.eval_generation USING btree (id);


--
-- Name: ix_eval_generation_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_generation_name ON public.eval_generation USING btree (name);


--
-- Name: ix_eval_generation_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_generation_source_id ON public.eval_generation USING btree (source_id);


--
-- Name: ix_eval_generation_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_generation_source_type ON public.eval_generation USING btree (source_type);


--
-- Name: ix_eval_results_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_results_created_time ON public.eval_results USING btree (created_time);


--
-- Name: ix_eval_results_hit_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_results_hit_rank ON public.eval_results USING btree (hit_rank);


--
-- Name: ix_eval_results_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_results_hostname ON public.eval_results USING btree (hostname);


--
-- Name: ix_eval_results_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_results_id ON public.eval_results USING btree (id);


--
-- Name: ix_eval_results_is_hit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_results_is_hit ON public.eval_results USING btree (is_hit);


--
-- Name: ix_eval_results_is_judge_hit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_results_is_judge_hit ON public.eval_results USING btree (is_judge_hit);


--
-- Name: ix_eval_results_question_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_results_question_id ON public.eval_results USING btree (question_id);


--
-- Name: ix_eval_results_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_results_run_id ON public.eval_results USING btree (run_id);


--
-- Name: ix_eval_retrieved_documents_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_retrieved_documents_document_id ON public.eval_retrieved_documents USING btree (document_id);


--
-- Name: ix_eval_retrieved_documents_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_retrieved_documents_id ON public.eval_retrieved_documents USING btree (id);


--
-- Name: ix_eval_retrieved_documents_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_retrieved_documents_rank ON public.eval_retrieved_documents USING btree (rank);


--
-- Name: ix_eval_retrieved_documents_result_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_retrieved_documents_result_id ON public.eval_retrieved_documents USING btree (result_id);


--
-- Name: ix_eval_runs_chunking_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_runs_chunking_strategy ON public.eval_runs USING btree (chunking_strategy);


--
-- Name: ix_eval_runs_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_runs_created_time ON public.eval_runs USING btree (created_time);


--
-- Name: ix_eval_runs_embedding_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_runs_embedding_name ON public.eval_runs USING btree (embedding_name);


--
-- Name: ix_eval_runs_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_runs_generation_id ON public.eval_runs USING btree (generation_id);


--
-- Name: ix_eval_runs_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_eval_runs_id ON public.eval_runs USING btree (id);


--
-- Name: ix_graph_extraction_logs_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_extraction_logs_created_time ON public.graph_extraction_logs USING btree (created_time);


--
-- Name: ix_graph_extraction_logs_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_extraction_logs_document_id ON public.graph_extraction_logs USING btree (document_id);


--
-- Name: ix_graph_extraction_logs_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_extraction_logs_id ON public.graph_extraction_logs USING btree (id);


--
-- Name: ix_graph_node_map_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_node_map_document_id ON public.graph_node_map USING btree (document_id);


--
-- Name: ix_graph_node_map_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_node_map_node_id ON public.graph_node_map USING btree (node_id);


--
-- Name: ix_graph_node_types_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_node_types_created_time ON public.graph_node_types USING btree (created_time);


--
-- Name: ix_graph_node_types_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_node_types_id ON public.graph_node_types USING btree (id);


--
-- Name: ix_graph_node_types_label; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_graph_node_types_label ON public.graph_node_types USING btree (label);


--
-- Name: ix_graph_nodes_canonical_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_nodes_canonical_name ON public.graph_nodes USING btree (canonical_name);


--
-- Name: ix_graph_nodes_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_nodes_created_time ON public.graph_nodes USING btree (created_time);


--
-- Name: ix_graph_nodes_embedding_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_nodes_embedding_name ON public.graph_nodes USING btree (embedding_name);


--
-- Name: ix_graph_nodes_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_nodes_id ON public.graph_nodes USING btree (id);


--
-- Name: ix_graph_nodes_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_nodes_name ON public.graph_nodes USING btree (name);


--
-- Name: ix_graph_nodes_type_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_nodes_type_id ON public.graph_nodes USING btree (type_id);


--
-- Name: ix_graph_relations_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_created_time ON public.graph_relations USING btree (created_time);


--
-- Name: ix_graph_relations_evidence_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_evidence_created_time ON public.graph_relations_evidence USING btree (created_time);


--
-- Name: ix_graph_relations_evidence_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_evidence_document_id ON public.graph_relations_evidence USING btree (document_id);


--
-- Name: ix_graph_relations_evidence_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_evidence_id ON public.graph_relations_evidence USING btree (id);


--
-- Name: ix_graph_relations_evidence_relation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_evidence_relation_id ON public.graph_relations_evidence USING btree (relation_id);


--
-- Name: ix_graph_relations_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_id ON public.graph_relations USING btree (id);


--
-- Name: ix_graph_relations_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_source_id ON public.graph_relations USING btree (source_id);


--
-- Name: ix_graph_relations_target_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_target_id ON public.graph_relations USING btree (target_id);


--
-- Name: ix_graph_relations_verb_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_relations_verb_id ON public.graph_relations USING btree (verb_id);


--
-- Name: ix_graph_verbs_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_verbs_created_time ON public.graph_verbs USING btree (created_time);


--
-- Name: ix_graph_verbs_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_verbs_id ON public.graph_verbs USING btree (id);


--
-- Name: ix_graph_verbs_inverse_verb_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_graph_verbs_inverse_verb_id ON public.graph_verbs USING btree (inverse_verb_id);


--
-- Name: ix_graph_verbs_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_graph_verbs_name ON public.graph_verbs USING btree (name);


--
-- Name: ix_llm_usage_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_created_time ON public.llm_usage USING btree (created_time);


--
-- Name: ix_llm_usage_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_document_id ON public.llm_usage USING btree (document_id);


--
-- Name: ix_llm_usage_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_model ON public.llm_usage USING btree (model);


--
-- Name: ix_llm_usage_raw_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_raw_document_id ON public.llm_usage USING btree (raw_document_id);


--
-- Name: ix_llm_usage_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_stage ON public.llm_usage USING btree (stage);


--
-- Name: ix_logs_chunk_embedding_chunk_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_chunk_embedding_chunk_strategy ON public.logs_chunk_embedding USING btree (chunk_strategy);


--
-- Name: ix_logs_chunk_embedding_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_chunk_embedding_document_id ON public.logs_chunk_embedding USING btree (document_id);


--
-- Name: ix_logs_chunk_embedding_embedding_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_chunk_embedding_embedding_name ON public.logs_chunk_embedding USING btree (embedding_name);


--
-- Name: ix_logs_chunk_embedding_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_chunk_embedding_hostname ON public.logs_chunk_embedding USING btree (hostname);


--
-- Name: ix_logs_chunk_embedding_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_chunk_embedding_id ON public.logs_chunk_embedding USING btree (id);


--
-- Name: ix_logs_chunk_embedding_insertion_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_chunk_embedding_insertion_time ON public.logs_chunk_embedding USING btree (insertion_time);


--
-- Name: ix_logs_parsing_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_parsing_document_id ON public.logs_parsing USING btree (document_id);


--
-- Name: ix_logs_parsing_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_parsing_hostname ON public.logs_parsing USING btree (hostname);


--
-- Name: ix_logs_parsing_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_parsing_id ON public.logs_parsing USING btree (id);


--
-- Name: ix_logs_parsing_insertion_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_parsing_insertion_time ON public.logs_parsing USING btree (insertion_time);


--
-- Name: ix_logs_search_chunking_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_search_chunking_strategy ON public.logs_search USING btree (chunking_strategy);


--
-- Name: ix_logs_search_created_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_search_created_time ON public.logs_search USING btree (created_time);


--
-- Name: ix_logs_search_doc_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_search_doc_type ON public.logs_search USING btree (doc_type);


--
-- Name: ix_logs_search_embedding_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_search_embedding_name ON public.logs_search USING btree (embedding_name);


--
-- Name: ix_logs_search_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_search_id ON public.logs_search USING btree (id);


--
-- Name: ix_logs_search_query; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_search_query ON public.logs_search USING btree (query);


--
-- Name: ix_logs_search_search_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_search_search_type ON public.logs_search USING btree (search_type);


--
-- Name: ix_logs_search_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_search_source_id ON public.logs_search USING btree (source_id);


--
-- Name: ix_logs_summary_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_summary_document_id ON public.logs_summary USING btree (document_id);


--
-- Name: ix_logs_summary_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_summary_hostname ON public.logs_summary USING btree (hostname);


--
-- Name: ix_logs_summary_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_summary_id ON public.logs_summary USING btree (id);


--
-- Name: ix_logs_summary_insertion_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_summary_insertion_time ON public.logs_summary USING btree (insertion_time);


--
-- Name: ix_logs_summary_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_logs_summary_model ON public.logs_summary USING btree (model);


--
-- Name: ix_parser_categories_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parser_categories_id ON public.parser_categories USING btree (id);


--
-- Name: ix_parser_categories_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parser_categories_source_id ON public.parser_categories USING btree (source_id);


--
-- Name: ix_parser_comparisons_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parser_comparisons_id ON public.parser_comparisons USING btree (id);


--
-- Name: ix_parser_comparisons_raw_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parser_comparisons_raw_document_id ON public.parser_comparisons USING btree (raw_document_id);


--
-- Name: ix_privacy_filters_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_privacy_filters_document_id ON public.privacy_filters USING btree (document_id);


--
-- Name: ix_privacy_filters_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_privacy_filters_id ON public.privacy_filters USING btree (id);


--
-- Name: ix_privacy_filters_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_privacy_filters_label ON public.privacy_filters USING btree (label);


--
-- Name: ix_privacy_filters_raw_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_privacy_filters_raw_document_id ON public.privacy_filters USING btree (raw_document_id);


--
-- Name: uq_embeddings_st_bgesmallenv1_5_chunk_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_embeddings_st_bgesmallenv1_5_chunk_id ON public.embeddings_st_bgesmallenv1_5 USING btree (chunk_id);


--
-- Name: uq_embeddings_st_minilml6v2_chunk_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_embeddings_st_minilml6v2_chunk_id ON public.embeddings_st_minilml6v2 USING btree (chunk_id);


--
-- Name: chunks chunks_text_search_vector_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER chunks_text_search_vector_update BEFORE INSERT OR UPDATE ON public.chunks FOR EACH ROW EXECUTE FUNCTION public.update_chunk_text_search_vector();


--
-- Name: chunks chunks_chunk_strategy_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_chunk_strategy_fkey FOREIGN KEY (chunk_strategy) REFERENCES public.chunk_strategies(strategy) ON DELETE SET NULL;


--
-- Name: chunks chunks_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_parser_outputs document_parser_outputs_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_parser_outputs
    ADD CONSTRAINT document_parser_outputs_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: documents documents_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: documents documents_parser_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_parser_id_fkey FOREIGN KEY (parser_id) REFERENCES public.parsers(name) ON DELETE SET NULL;


--
-- Name: documents documents_raw_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_raw_document_id_fkey FOREIGN KEY (raw_document_id) REFERENCES public.documents_raw(id) ON DELETE SET NULL;


--
-- Name: documents_raw documents_raw_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents_raw
    ADD CONSTRAINT documents_raw_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id) ON DELETE RESTRICT;


--
-- Name: documents documents_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id) ON DELETE RESTRICT;


--
-- Name: embeddings_st_bgesmallenv1_5 embeddings_st_bgesmallenv1_5_chunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings_st_bgesmallenv1_5
    ADD CONSTRAINT embeddings_st_bgesmallenv1_5_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.chunks(id) ON DELETE CASCADE;


--
-- Name: embeddings_st_minilml6v2 embeddings_st_minilml6v2_chunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings_st_minilml6v2
    ADD CONSTRAINT embeddings_st_minilml6v2_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.chunks(id) ON DELETE CASCADE;


--
-- Name: eval_audit eval_audit_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_audit
    ADD CONSTRAINT eval_audit_question_id_fkey FOREIGN KEY (question_id) REFERENCES public.eval_dataset(id) ON DELETE CASCADE;


--
-- Name: eval_dataset eval_dataset_generation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_dataset
    ADD CONSTRAINT eval_dataset_generation_id_fkey FOREIGN KEY (generation_id) REFERENCES public.eval_generation(id) ON DELETE SET NULL;


--
-- Name: eval_dataset eval_dataset_source_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_dataset
    ADD CONSTRAINT eval_dataset_source_document_id_fkey FOREIGN KEY (source_document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: eval_results eval_results_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_results
    ADD CONSTRAINT eval_results_question_id_fkey FOREIGN KEY (question_id) REFERENCES public.eval_dataset(id) ON DELETE CASCADE;


--
-- Name: eval_results eval_results_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_results
    ADD CONSTRAINT eval_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.eval_runs(id) ON DELETE CASCADE;


--
-- Name: eval_retrieved_documents eval_retrieved_documents_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_retrieved_documents
    ADD CONSTRAINT eval_retrieved_documents_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: eval_retrieved_documents eval_retrieved_documents_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_retrieved_documents
    ADD CONSTRAINT eval_retrieved_documents_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.eval_results(id) ON DELETE CASCADE;


--
-- Name: eval_runs eval_runs_generation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eval_runs
    ADD CONSTRAINT eval_runs_generation_id_fkey FOREIGN KEY (generation_id) REFERENCES public.eval_generation(id) ON DELETE SET NULL;


--
-- Name: graph_extraction_logs graph_extraction_logs_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_extraction_logs
    ADD CONSTRAINT graph_extraction_logs_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: graph_node_map graph_node_map_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_node_map
    ADD CONSTRAINT graph_node_map_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: graph_node_map graph_node_map_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_node_map
    ADD CONSTRAINT graph_node_map_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.graph_nodes(id) ON DELETE CASCADE;


--
-- Name: graph_nodes graph_nodes_embedding_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_nodes
    ADD CONSTRAINT graph_nodes_embedding_name_fkey FOREIGN KEY (embedding_name) REFERENCES public.embedding_configs(short_name) ON DELETE SET NULL;


--
-- Name: graph_nodes graph_nodes_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_nodes
    ADD CONSTRAINT graph_nodes_type_id_fkey FOREIGN KEY (type_id) REFERENCES public.graph_node_types(id) ON DELETE RESTRICT;


--
-- Name: graph_relations_evidence graph_relations_evidence_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_relations_evidence
    ADD CONSTRAINT graph_relations_evidence_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: graph_relations_evidence graph_relations_evidence_relation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_relations_evidence
    ADD CONSTRAINT graph_relations_evidence_relation_id_fkey FOREIGN KEY (relation_id) REFERENCES public.graph_relations(id) ON DELETE CASCADE;


--
-- Name: graph_relations graph_relations_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_relations
    ADD CONSTRAINT graph_relations_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.graph_nodes(id) ON DELETE CASCADE;


--
-- Name: graph_relations graph_relations_target_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_relations
    ADD CONSTRAINT graph_relations_target_id_fkey FOREIGN KEY (target_id) REFERENCES public.graph_nodes(id) ON DELETE CASCADE;


--
-- Name: graph_relations graph_relations_verb_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_relations
    ADD CONSTRAINT graph_relations_verb_id_fkey FOREIGN KEY (verb_id) REFERENCES public.graph_verbs(id) ON DELETE RESTRICT;


--
-- Name: graph_verbs graph_verbs_inverse_verb_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.graph_verbs
    ADD CONSTRAINT graph_verbs_inverse_verb_id_fkey FOREIGN KEY (inverse_verb_id) REFERENCES public.graph_verbs(id) ON DELETE SET NULL;


--
-- Name: llm_usage llm_usage_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage
    ADD CONSTRAINT llm_usage_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: llm_usage llm_usage_raw_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage
    ADD CONSTRAINT llm_usage_raw_document_id_fkey FOREIGN KEY (raw_document_id) REFERENCES public.documents_raw(id) ON DELETE SET NULL;


--
-- Name: logs_chunk_embedding logs_chunk_embedding_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.logs_chunk_embedding
    ADD CONSTRAINT logs_chunk_embedding_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: logs_parsing logs_parsing_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.logs_parsing
    ADD CONSTRAINT logs_parsing_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: logs_summary logs_summary_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.logs_summary
    ADD CONSTRAINT logs_summary_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: parser_categories parser_categories_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parser_categories
    ADD CONSTRAINT parser_categories_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id) ON DELETE CASCADE;


--
-- Name: parser_comparisons parser_comparisons_raw_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parser_comparisons
    ADD CONSTRAINT parser_comparisons_raw_document_id_fkey FOREIGN KEY (raw_document_id) REFERENCES public.documents_raw(id) ON DELETE CASCADE;


--
-- Name: privacy_filters privacy_filters_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.privacy_filters
    ADD CONSTRAINT privacy_filters_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: privacy_filters privacy_filters_raw_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.privacy_filters
    ADD CONSTRAINT privacy_filters_raw_document_id_fkey FOREIGN KEY (raw_document_id) REFERENCES public.documents_raw(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict yPxEO56EZG4561VYYIdfbnDW6RnzbkjbSuogu2b6aJgFTSH3Ahg8632zQtw697Z

