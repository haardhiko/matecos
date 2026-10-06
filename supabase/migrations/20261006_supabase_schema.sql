-- ==============================================================================
-- OrchaDeck Supabase Database Schema & Row Level Security (RLS) Migration
-- ==============================================================================
-- Run this SQL in the Supabase Dashboard SQL Editor (or via database migrations)
-- to provision user_profiles, repositories, tool_imports, and user_tools with RLS.
-- ==============================================================================

-- 1. User Profiles
CREATE TABLE IF NOT EXISTS public.user_profiles (
    id TEXT PRIMARY KEY, -- References auth.users(id)
    email TEXT,
    full_name TEXT,
    avatar_url TEXT,
    provider TEXT NOT NULL DEFAULT 'github',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS ix_user_profiles_email ON public.user_profiles(email);

-- 2. Repositories (Owned by user)
CREATE TABLE IF NOT EXISTS public.repositories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT 'main',
    status TEXT NOT NULL DEFAULT 'pending', -- pending, processing, success, partial, failed
    total_files INTEGER NOT NULL DEFAULT 0,
    languages JSONB NOT NULL DEFAULT '[]'::jsonb,
    frameworks JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS ix_repositories_user_id ON public.repositories(user_id);
CREATE INDEX IF NOT EXISTS ix_repositories_name ON public.repositories(name);

-- 3. Tool Imports (Historical import runs & logs)
CREATE TABLE IF NOT EXISTS public.tool_imports (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    repository_id TEXT NOT NULL REFERENCES public.repositories(id) ON DELETE CASCADE,
    status TEXT NOT NULL, -- pending, processing, success, partial, failed
    tools_discovered INTEGER NOT NULL DEFAULT 0,
    tools_registered INTEGER NOT NULL DEFAULT 0,
    tools_failed INTEGER NOT NULL DEFAULT 0,
    dependency_status TEXT NOT NULL DEFAULT 'unknown',
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS ix_tool_imports_user_id ON public.tool_imports(user_id);
CREATE INDEX IF NOT EXISTS ix_tool_imports_repository_id ON public.tool_imports(repository_id);
CREATE INDEX IF NOT EXISTS ix_tool_imports_status ON public.tool_imports(status);

-- 4. User Tools (Extracted & registered tools)
CREATE TABLE IF NOT EXISTS public.user_tools (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    repository_id TEXT REFERENCES public.repositories(id) ON DELETE CASCADE,
    tool_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    language TEXT NOT NULL DEFAULT 'unknown',
    entry_point TEXT NOT NULL DEFAULT '',
    invocation_method TEXT NOT NULL DEFAULT 'builtin',
    risk_level TEXT NOT NULL DEFAULT 'low',
    version TEXT NOT NULL DEFAULT '0.1.0',
    source_url TEXT,
    manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS ix_user_tools_user_id ON public.user_tools(user_id);
CREATE INDEX IF NOT EXISTS ix_user_tools_tool_id ON public.user_tools(tool_id);
CREATE INDEX IF NOT EXISTS ix_user_tools_repository_id ON public.user_tools(repository_id);

-- ==============================================================================
-- 5. Row Level Security (RLS) Configuration
-- ==============================================================================

ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.repositories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tool_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_tools ENABLE ROW LEVEL SECURITY;

-- User Profiles Policies
DROP POLICY IF EXISTS user_profiles_select_policy ON public.user_profiles;
CREATE POLICY user_profiles_select_policy ON public.user_profiles
    FOR SELECT USING (id = auth.uid()::text);

DROP POLICY IF EXISTS user_profiles_insert_policy ON public.user_profiles;
CREATE POLICY user_profiles_insert_policy ON public.user_profiles
    FOR INSERT WITH CHECK (id = auth.uid()::text);

DROP POLICY IF EXISTS user_profiles_update_policy ON public.user_profiles;
CREATE POLICY user_profiles_update_policy ON public.user_profiles
    FOR UPDATE USING (id = auth.uid()::text);

-- Repositories Policies (User can only read, insert, update, delete their own repos)
DROP POLICY IF EXISTS repositories_select_policy ON public.repositories;
CREATE POLICY repositories_select_policy ON public.repositories
    FOR SELECT USING (user_id = auth.uid()::text);

DROP POLICY IF EXISTS repositories_insert_policy ON public.repositories;
CREATE POLICY repositories_insert_policy ON public.repositories
    FOR INSERT WITH CHECK (user_id = auth.uid()::text);

DROP POLICY IF EXISTS repositories_update_policy ON public.repositories;
CREATE POLICY repositories_update_policy ON public.repositories
    FOR UPDATE USING (user_id = auth.uid()::text);

DROP POLICY IF EXISTS repositories_delete_policy ON public.repositories;
CREATE POLICY repositories_delete_policy ON public.repositories
    FOR DELETE USING (user_id = auth.uid()::text);

-- Tool Imports Policies
DROP POLICY IF EXISTS tool_imports_select_policy ON public.tool_imports;
CREATE POLICY tool_imports_select_policy ON public.tool_imports
    FOR SELECT USING (user_id = auth.uid()::text);

DROP POLICY IF EXISTS tool_imports_insert_policy ON public.tool_imports;
CREATE POLICY tool_imports_insert_policy ON public.tool_imports
    FOR INSERT WITH CHECK (user_id = auth.uid()::text);

DROP POLICY IF EXISTS tool_imports_delete_policy ON public.tool_imports;
CREATE POLICY tool_imports_delete_policy ON public.tool_imports
    FOR DELETE USING (user_id = auth.uid()::text);

-- User Tools Policies
DROP POLICY IF EXISTS user_tools_select_policy ON public.user_tools;
CREATE POLICY user_tools_select_policy ON public.user_tools
    FOR SELECT USING (user_id = auth.uid()::text);

DROP POLICY IF EXISTS user_tools_insert_policy ON public.user_tools;
CREATE POLICY user_tools_insert_policy ON public.user_tools
    FOR INSERT WITH CHECK (user_id = auth.uid()::text);

DROP POLICY IF EXISTS user_tools_update_policy ON public.user_tools;
CREATE POLICY user_tools_update_policy ON public.user_tools
    FOR UPDATE USING (user_id = auth.uid()::text);

DROP POLICY IF EXISTS user_tools_delete_policy ON public.user_tools;
CREATE POLICY user_tools_delete_policy ON public.user_tools
    FOR DELETE USING (user_id = auth.uid()::text);
