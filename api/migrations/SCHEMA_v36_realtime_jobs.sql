-- SCHEMA_v36: enable Realtime on public.jobs
--
-- store_puller.py subscribes to postgres_changes on public.jobs (filtered to
-- assigned_store_id) so a routed/paid job wakes the store PC's poll loop
-- immediately instead of waiting for the fallback poll. That subscription
-- connects fine either way -- Supabase Realtime only actually emits an event
-- for a table once it is added to the supabase_realtime publication, which
-- jobs was not. Without this, store_puller silently runs on the 15-minute
-- fallback poll only.

ALTER PUBLICATION supabase_realtime ADD TABLE public.jobs;
