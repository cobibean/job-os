BEGIN TRANSACTION;
CREATE TABLE career_profile_audit_events (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_principal TEXT NOT NULL,
                action TEXT NOT NULL,
                profile_revision INTEGER NOT NULL CHECK (profile_revision >= 0),
                base_profile_revision INTEGER,
                affected_fields_json TEXT NOT NULL CHECK (json_valid(affected_fields_json)),
                revision_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE career_profile_authority_idempotency (
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
                result_json TEXT NOT NULL CHECK (json_valid(result_json)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_change_proposals (
                proposal_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                review_reason TEXT NOT NULL,
                base_profile_revision INTEGER NOT NULL CHECK (base_profile_revision >= 0),
                operation TEXT NOT NULL CHECK (
                    operation IN ('item.create', 'item.update', 'item.remove')
                ),
                target_id TEXT NOT NULL,
                before_json TEXT CHECK (before_json IS NULL OR json_valid(before_json)),
                after_json TEXT CHECK (after_json IS NULL OR json_valid(after_json)),
                evidence_ids_json TEXT NOT NULL CHECK (json_valid(evidence_ids_json)),
                payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
                status TEXT NOT NULL DEFAULT 'pending' CHECK (
                    status IN ('pending', 'accepted', 'rejected')
                ),
                created_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by_principal TEXT,
                FOREIGN KEY(agent_id) REFERENCES career_profile_connected_agents(agent_id)
            );
CREATE TABLE career_profile_collaboration_idempotency (
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                result_json TEXT NOT NULL CHECK (json_valid(result_json)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_complete_idempotency (
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                result_json TEXT NOT NULL CHECK (json_valid(result_json)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_complete_revisions (
                revision_id TEXT PRIMARY KEY,
                profile_revision INTEGER NOT NULL UNIQUE CHECK (profile_revision >= 1),
                base_profile_revision INTEGER NOT NULL CHECK (base_profile_revision >= 0),
                actor_principal TEXT NOT NULL,
                operation TEXT NOT NULL CHECK (
                    operation IN (
                        'item.upsert', 'item.remove', 'evidence.import', 'evidence.remove'
                    )
                ),
                item_id TEXT,
                evidence_id TEXT,
                before_json TEXT CHECK (before_json IS NULL OR json_valid(before_json)),
                after_json TEXT CHECK (after_json IS NULL OR json_valid(after_json)),
                affected_fields_json TEXT NOT NULL CHECK (json_valid(affected_fields_json)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            , reason TEXT, proposal_id TEXT, undo_of_revision_id TEXT, actor_kind TEXT);
CREATE TABLE career_profile_connected_agents (
                agent_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                principal TEXT NOT NULL UNIQUE,
                token_sha256 TEXT NOT NULL CHECK (length(token_sha256) = 64),
                trust_mode TEXT NOT NULL DEFAULT 'review' CHECK (
                    trust_mode IN ('review', 'direct')
                ),
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                connected_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                disconnected_at TEXT
            );
CREATE TABLE career_profile_context_grants (
                agent_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'none' CHECK (
                    mode IN ('none', 'selected', 'broader')
                ),
                selected_item_ids_json TEXT NOT NULL DEFAULT '[]'
                    CHECK (json_valid(selected_item_ids_json)),
                selected_areas_json TEXT NOT NULL DEFAULT '[]'
                    CHECK (json_valid(selected_areas_json)),
                updated_at TEXT NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES career_profile_connected_agents(agent_id)
            );
CREATE TABLE career_profile_context_idempotency (
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
                result_json TEXT NOT NULL CHECK (json_valid(result_json)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_context_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                profile_revision INTEGER NOT NULL CHECK (profile_revision >= 0),
                authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 0),
                scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
                content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
                projection_json TEXT NOT NULL CHECK (json_valid(projection_json)),
                created_at TEXT NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES career_profile_connected_agents(agent_id)
            );
CREATE TABLE career_profile_erasure_journal (
                operation_id TEXT PRIMARY KEY,
                operation TEXT NOT NULL CHECK (
                    operation IN ('evidence.erase', 'profile.reset')
                ),
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                target_evidence_id TEXT,
                storage_names_json TEXT NOT NULL CHECK (json_valid(storage_names_json)),
                phase TEXT NOT NULL DEFAULT 'prepared' CHECK (phase IN ('prepared', 'purged')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_erasure_receipts (
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                result_json TEXT NOT NULL CHECK (json_valid(result_json)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_evidence (
                evidence_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                byte_count INTEGER NOT NULL CHECK (byte_count > 0),
                captured_at TEXT,
                imported_at TEXT NOT NULL,
                provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
                storage_name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
            );
CREATE TABLE career_profile_idempotency (
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                result_json TEXT NOT NULL CHECK (json_valid(result_json)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_intent_grants (
                grant_id TEXT PRIMARY KEY,
                created_by_principal TEXT NOT NULL,
                operation TEXT NOT NULL,
                target_id TEXT,
                payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
                created_at TEXT NOT NULL,
                consumed_at TEXT,
                consumed_by_principal TEXT,
                CHECK (
                    (consumed_at IS NULL AND consumed_by_principal IS NULL)
                    OR (consumed_at IS NOT NULL AND consumed_by_principal IS NOT NULL)
                )
            );
CREATE TABLE career_profile_items (
                item_id TEXT PRIMARY KEY,
                value_json TEXT NOT NULL CHECK (json_valid(value_json)),
                provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
                review_status TEXT NOT NULL CHECK (
                    review_status IN ('accepted', 'proposed', 'conflicting')
                ),
                evidence_ids_json TEXT NOT NULL CHECK (json_valid(evidence_ids_json)),
                item_revision INTEGER NOT NULL CHECK (item_revision >= 1),
                actor_principal TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
CREATE TABLE career_profile_migration_journal (
                bundle_sha256 TEXT PRIMARY KEY CHECK (length(bundle_sha256) = 64),
                actor_principal TEXT NOT NULL CHECK (actor_principal = 'migration:career-profile'),
                phase TEXT NOT NULL CHECK (phase IN ('prepared', 'vault_written', 'complete')),
                request_json TEXT NOT NULL CHECK (json_valid(request_json)),
                report_json TEXT CHECK (report_json IS NULL OR json_valid(report_json)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
CREATE TABLE career_profile_migration_receipts (
                bundle_sha256 TEXT PRIMARY KEY CHECK (length(bundle_sha256) = 64),
                report_json TEXT NOT NULL CHECK (json_valid(report_json)),
                created_at TEXT NOT NULL
            );
CREATE TABLE career_profile_records (
                record_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                namespace TEXT NOT NULL,
                item_revision INTEGER NOT NULL CHECK (item_revision >= 1),
                value_json TEXT NOT NULL CHECK (json_valid(value_json)),
                actor_principal TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(profile_id, namespace),
                FOREIGN KEY(profile_id) REFERENCES career_profiles(profile_id)
            );
CREATE TABLE career_profile_restore_journal (
                operation_id TEXT PRIMARY KEY,
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
                phase TEXT NOT NULL CHECK (phase IN ('swap_pending', 'db_committed')),
                had_live_vault INTEGER NOT NULL CHECK (had_live_vault IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_restore_receipts (
                actor_principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
                result_json TEXT CHECK (
                    result_json IS NULL OR json_valid(result_json)
                ),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(actor_principal, idempotency_key)
            );
CREATE TABLE career_profile_revisions (
                revision_id TEXT PRIMARY KEY,
                profile_revision INTEGER NOT NULL UNIQUE CHECK (profile_revision >= 1),
                profile_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                namespace TEXT NOT NULL,
                item_revision INTEGER NOT NULL CHECK (item_revision >= 1),
                actor_principal TEXT NOT NULL,
                base_profile_revision INTEGER NOT NULL CHECK (base_profile_revision >= 0),
                operation TEXT NOT NULL CHECK (operation IN ('set', 'restore')),
                previous_value_json TEXT CHECK (
                    previous_value_json IS NULL OR json_valid(previous_value_json)
                ),
                resulting_value_json TEXT NOT NULL CHECK (json_valid(resulting_value_json)),
                changed_fields_json TEXT NOT NULL CHECK (json_valid(changed_fields_json)),
                restored_from_profile_revision INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(profile_id) REFERENCES career_profiles(profile_id)
            );
CREATE TABLE career_profile_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                profile_revision INTEGER NOT NULL CHECK (profile_revision >= 0),
                content_hash TEXT NOT NULL,
                authorized_principal TEXT NOT NULL,
                scopes_json TEXT NOT NULL CHECK (json_valid(scopes_json)),
                projection_json TEXT NOT NULL CHECK (json_valid(projection_json)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE career_profiles (
                profile_id TEXT PRIMARY KEY CHECK (profile_id = 'career_profile_global'),
                head_revision INTEGER NOT NULL DEFAULT 0 CHECK (head_revision >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            , authority_epoch INTEGER NOT NULL DEFAULT 0
                CHECK (authority_epoch >= 0), authority_state TEXT NOT NULL DEFAULT 'staging'
                CHECK (authority_state IN ('staging', 'cutover')));
CREATE TABLE conversation_continuation_bindings (
                conversation_id TEXT NOT NULL,
                continuation_digest TEXT NOT NULL,
                source_turn_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (conversation_id, continuation_digest),
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
                FOREIGN KEY (source_turn_id) REFERENCES conversation_turns(turn_id)
            );
CREATE TABLE conversation_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                turn_id TEXT,
                event_type TEXT NOT NULL,
                state TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                source_event_id TEXT,
                occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
                FOREIGN KEY(conversation_id, turn_id)
                    REFERENCES conversation_turns(conversation_id, turn_id)
            );
CREATE TABLE conversation_turns (
                turn_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                source_turn_id TEXT,
                text TEXT NOT NULL,
                context_json TEXT NOT NULL,
                status TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, career_profile_snapshot_id TEXT, career_profile_revision INTEGER
               CHECK (career_profile_revision >= 0), career_profile_content_hash TEXT, career_profile_context_snapshot_id TEXT, career_profile_context_agent_id TEXT, career_profile_context_revision INTEGER
               CHECK (
                   career_profile_context_revision IS NULL
                   OR career_profile_context_revision >= 0
               ), career_profile_context_authority_epoch INTEGER
               CHECK (
                   career_profile_context_authority_epoch IS NULL
                   OR career_profile_context_authority_epoch >= 0
               ), career_profile_context_content_hash TEXT
               CHECK (
                   career_profile_context_content_hash IS NULL
                   OR length(career_profile_context_content_hash) = 64
               ),
                CHECK (status IN (
                    'queued', 'running', 'waiting', 'completed', 'failed', 'interrupted'
                )),
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
                UNIQUE(conversation_id, turn_id),
                FOREIGN KEY(conversation_id, source_turn_id)
                    REFERENCES conversation_turns(conversation_id, turn_id)
            );
CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                position INTEGER NOT NULL CHECK (position >= 1),
                title TEXT NOT NULL,
                stored_session_id TEXT,
                recovery_turn_id TEXT,
                isolated_turn_id TEXT,
                isolated_previous_session_id TEXT,
                isolated_agent_session_id TEXT,
                ignored_agent_session_id TEXT,
                archived_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            , owner_device_id TEXT NOT NULL
                DEFAULT '__migration_owner__', selected_job_id TEXT, active_artifact_id TEXT, active_artifact_page
               INTEGER NOT NULL DEFAULT 1 CHECK (active_artifact_page >= 1), active_artifact_zoom
               REAL NOT NULL DEFAULT 1.0
               CHECK (active_artifact_zoom >= 0.5 AND active_artifact_zoom <= 3.0));
CREATE TABLE document_artifacts (
                artifact_id TEXT PRIMARY KEY,
                registry_key TEXT NOT NULL UNIQUE,
                job_id TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                artifact_revision TEXT NOT NULL,
                media_type TEXT NOT NULL,
                sha256 TEXT,
                render_status TEXT NOT NULL,
                canonical_path TEXT,
                filename TEXT,
                failure_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, document_key TEXT NOT NULL DEFAULT 'resume', document_label TEXT NOT NULL DEFAULT 'Resume', render_sequence INTEGER NOT NULL DEFAULT 0, editable_document_id TEXT, editable_document_revision INTEGER,
                CHECK (render_status IN ('succeeded', 'failed', 'rendering'))
            );
CREATE TABLE document_file_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                observed_revision INTEGER NOT NULL CHECK (observed_revision >= 1),
                observed_device_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                filename TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                UNIQUE(document_id, observed_device_id, observed_revision),
                FOREIGN KEY(document_id) REFERENCES document_files(document_id) ON DELETE CASCADE
            );
CREATE TABLE document_files (
                document_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                document_key TEXT NOT NULL,
                document_label TEXT NOT NULL,
                filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                observed_revision INTEGER NOT NULL CHECK (observed_revision >= 1),
                capabilities_json TEXT NOT NULL,
                observed_at TEXT NOT NULL, observed_device_id TEXT NOT NULL DEFAULT 'legacy',
                CHECK (document_key IN ('resume', 'cover_letter', 'references')),
                UNIQUE(job_id, document_key)
            );
CREATE TABLE document_revision_approvals (
                job_id TEXT NOT NULL,
                document_key TEXT NOT NULL CHECK (document_key IN ('resume', 'cover_letter')),
                source_revision TEXT NOT NULL,
                artifact_manifest_json TEXT NOT NULL CHECK (json_valid(artifact_manifest_json)),
                approved_by TEXT NOT NULL CHECK (approved_by = 'user'),
                approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(job_id, document_key)
            );
CREATE TABLE editable_document_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                document_revision INTEGER NOT NULL CHECK (document_revision >= 1),
                reason TEXT NOT NULL,
                actor TEXT NOT NULL,
                label TEXT,
                content_json TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                comments_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (reason IN (
                    'import', 'before_agent_edit', 'manual', 'before_publish', 'before_restore'
                )),
                CHECK (actor IN ('user', 'jobhunter', 'import', 'system')),
                FOREIGN KEY(document_id)
                    REFERENCES editable_documents(document_id) ON DELETE CASCADE
            );
CREATE TABLE editable_documents (
                document_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                document_key TEXT NOT NULL,
                document_label TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                content_json TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                comments_json TEXT NOT NULL DEFAULT '[]',
                import_report_json TEXT NOT NULL DEFAULT '{"issues":[]}',
                source_artifact_id TEXT,
                source_filename TEXT,
                source_sha256 TEXT,
                published_revision INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (document_key IN ('resume', 'cover_letter', 'references')),
                UNIQUE(job_id, document_key),
                FOREIGN KEY(source_artifact_id) REFERENCES document_artifacts(artifact_id)
            );
CREATE TABLE job_document_state (
                job_id TEXT PRIMARY KEY,
                current_artifact_id TEXT,
                last_successful_artifact_id TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, approved_artifact_id TEXT, approved_at TEXT,
                FOREIGN KEY(current_artifact_id) REFERENCES document_artifacts(artifact_id),
                FOREIGN KEY(last_successful_artifact_id) REFERENCES document_artifacts(artifact_id)
            );
CREATE TABLE job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                job_id TEXT,
                origin TEXT NOT NULL,
                occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                payload_json TEXT NOT NULL DEFAULT '{}'
            , actor_id TEXT, target_resource TEXT, command_name TEXT, outcome TEXT, idempotency_key TEXT, request_hash TEXT, result_json TEXT);
CREATE TABLE job_workspace (
                workspace_id INTEGER PRIMARY KEY CHECK (workspace_id = 1),
                selected_job_id TEXT,
                sort_mode TEXT NOT NULL DEFAULT 'manual',
                manual_order_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
INSERT INTO "job_workspace" VALUES(1,'(FAKE)-job-legacy-1','manual','[]','2026-08-01T12:00:00Z');
CREATE TABLE jobos_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            );
INSERT INTO "jobos_metadata" VALUES('installation_profile_id','jprof_11111111111111111111111111111111','2026-08-01T12:00:00Z');
CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
INSERT INTO "schema_migrations" VALUES(1,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(2,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(3,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(4,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(5,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(6,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(7,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(8,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(9,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(10,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(11,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(12,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(13,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(14,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(15,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(16,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(17,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(18,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(19,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(20,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(21,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(22,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(23,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(24,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(25,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(26,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(27,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(28,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(29,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(30,'2026-08-01T12:00:00Z');
INSERT INTO "schema_migrations" VALUES(31,'2026-08-01T12:00:00Z');
CREATE TABLE workspace_snapshots (
                device_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
CREATE UNIQUE INDEX job_events_mutation_idempotency
            ON job_events(actor_id, target_resource, command_name, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            ;
CREATE INDEX document_artifacts_job ON document_artifacts(job_id, created_at);
CREATE INDEX editable_documents_job ON editable_documents(job_id, document_key);
CREATE INDEX editable_document_snapshots_document
                ON editable_document_snapshots(document_id, created_at DESC)
            ;
CREATE INDEX document_files_job ON document_files(job_id, document_key);
CREATE UNIQUE INDEX document_artifacts_editable_publication_media
            ON document_artifacts(
                editable_document_id, editable_document_revision, media_type
            )
            WHERE editable_document_id IS NOT NULL
                AND editable_document_revision IS NOT NULL
                AND render_status = 'succeeded'
            ;
CREATE INDEX conversation_turns_scope_status
            ON conversation_turns(conversation_id, status, created_at)
            ;
CREATE UNIQUE INDEX conversation_events_scope_source
            ON conversation_events(conversation_id, source_event_id)
            WHERE source_event_id IS NOT NULL
            ;
CREATE UNIQUE INDEX conversations_owner_active_position
            ON conversations(owner_device_id, position) WHERE archived_at IS NULL
            ;
CREATE INDEX conversations_selected_job
               ON conversations(selected_job_id) WHERE selected_job_id IS NOT NULL;
CREATE INDEX career_profile_revisions_record
               ON career_profile_revisions(record_id, profile_revision DESC);
CREATE INDEX career_profile_snapshots_principal
               ON career_profile_snapshots(authorized_principal, created_at DESC);
CREATE INDEX conversation_turns_career_profile_snapshot
               ON conversation_turns(career_profile_snapshot_id)
               WHERE career_profile_snapshot_id IS NOT NULL;
CREATE INDEX career_profile_items_active
               ON career_profile_items(active, created_at, item_id);
CREATE INDEX career_profile_change_proposals_status
               ON career_profile_change_proposals(status, created_at, proposal_id);
CREATE INDEX career_profile_context_snapshots_agent
               ON career_profile_context_snapshots(agent_id, created_at DESC);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('document_file_observations',0);
INSERT INTO "sqlite_sequence" VALUES('conversation_events',2);
INSERT INTO "conversations" VALUES('conv_current',1,'(FAKE) Existing Hermes Chat','(FAKE)-opaque-hermes-session-1',NULL,NULL,NULL,NULL,NULL,NULL,'2026-08-01T12:00:00Z','2026-08-01T12:00:00Z','(FAKE)-authorized-macbook','(FAKE)-job-legacy-1',NULL,1,1.0);
INSERT INTO "conversation_turns" VALUES('(FAKE)-turn-legacy-1','conv_current','(FAKE)-message-legacy-1',NULL,'(FAKE) Summarize the synthetic role.','{"selected_job_id":"(FAKE)-job-legacy-1"}','completed',0,'2026-08-01T12:00:00Z','2026-08-01T12:00:00Z',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "conversation_events" VALUES(1,'conv_current','(FAKE)-turn-legacy-1','user_message','working','(FAKE) User message','{"text":"(FAKE) Summarize the synthetic role."}','(FAKE)-source-event-1','2026-08-01T12:00:00Z');
INSERT INTO "conversation_events" VALUES(2,'conv_current','(FAKE)-turn-legacy-1','assistant_message','completed','(FAKE) Assistant response','{"text":"(FAKE) Synthetic role summary."}','(FAKE)-source-event-2','2026-08-01T12:00:00Z');
COMMIT;
