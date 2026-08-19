-- SCHEMA_v37: enable Realtime on academic_orders and manuscript_transcripts
--
-- Mirrors SCHEMA_v36 (public.jobs): academic_pipeline_worker.py and
-- tools/cloud_transcription_worker.py now subscribe to postgres_changes on
-- their respective tables so a status change wakes each store-PC worker
-- immediately instead of waiting for the fallback poll. That subscription
-- connects fine either way -- Supabase Realtime only actually emits an
-- event for a table once it is added to the supabase_realtime publication,
-- which neither table was. Without this, both workers silently run on
-- their 15-minute fallback poll only.

ALTER PUBLICATION supabase_realtime ADD TABLE public.academic_orders;
ALTER PUBLICATION supabase_realtime ADD TABLE public.manuscript_transcripts;
