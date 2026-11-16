begin;

create table if not exists public.cf_settings (
  id integer primary key default 1 check (id = 1),
  clip_seconds integer not null default 30 check (clip_seconds between 10 and 90),
  clip_count integer not null default 10 check (clip_count between 1 and 10),
  aspect text not null default 'portrait' check (aspect in ('landscape', 'portrait'))
);
insert into public.cf_settings (id) values (1) on conflict do nothing;
alter table public.cf_settings add column if not exists twitch_segment_minutes integer
  not null default 60 check (twitch_segment_minutes in (15, 60));
alter table public.cf_settings alter column clip_count set default 10;
alter table public.cf_settings alter column aspect set default 'portrait';
update public.cf_settings set aspect = 'portrait' where aspect <> 'portrait';
update public.cf_settings set clip_count = 10, clip_seconds = least(45, greatest(15, clip_seconds))
  where clip_count <> 10 or clip_seconds not between 15 and 45;

create table if not exists public.cf_campaigns (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(name) between 1 and 120),
  brand text not null check (char_length(brand) between 1 and 120),
  description text not null default '' check (char_length(description) <= 2000),
  status text not null default 'draft' check (status in ('draft', 'active', 'paused', 'finished')),
  created_at timestamptz not null default now()
);
alter table public.cf_campaigns
  add column if not exists promo_code text not null default '' check (char_length(promo_code) <= 32),
  add column if not exists cpm numeric(12,4) not null default 0 check (cpm between 0 and 1000000),
  add column if not exists currency text not null default 'RUB' check (currency in ('RUB', 'USD', 'EUR')),
  add column if not exists starts_on date,
  add column if not exists ends_on date check (starts_on is null or ends_on is null or ends_on >= starts_on),
  add column if not exists expected_views bigint not null default 0 check (expected_views between 0 and 1000000000),
  add column if not exists banner_id uuid,
  add column if not exists placement jsonb not null default '{}'::jsonb check (jsonb_typeof(placement) = 'object');

create table if not exists public.cf_sources (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  extension text not null check (extension in ('.mp4', '.mov', '.mkv', '.webm', '.avi', '.flv', '.m4v', '.ts')),
  byte_size bigint not null check (byte_size > 0),
  duration double precision not null check (duration > 0),
  has_audio boolean not null,
  campaign_id uuid references public.cf_campaigns(id) on delete set null,
  status text not null default 'queued' check (status in ('queued', 'processing', 'completed', 'failed')),
  progress integer not null default 0 check (progress between 0 and 100),
  error text,
  attempts integer not null default 0,
  lease_token uuid,
  lease_until timestamptz,
  clip_seconds integer not null check (clip_seconds between 10 and 90),
  clip_count integer not null check (clip_count between 1 and 10),
  aspect text not null check (aspect in ('landscape', 'portrait')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists cf_sources_queue_idx on public.cf_sources (created_at)
  where status in ('queued', 'processing');
alter table public.cf_sources add column if not exists twitch_capture boolean not null default false;
create index if not exists cf_sources_campaign_idx on public.cf_sources (campaign_id);
create index if not exists cf_sources_created_idx on public.cf_sources (created_at desc, id desc);

create table if not exists public.cf_clips (
  id uuid primary key,
  source_id uuid not null references public.cf_sources(id) on delete cascade,
  title text not null,
  start_seconds double precision not null check (start_seconds >= 0),
  end_seconds double precision not null check (end_seconds > start_seconds),
  score double precision not null check (score between 0 and 1),
  created_at timestamptz not null default now()
);
alter table public.cf_clips
  add column if not exists category text check (category in ('funny', 'shout', 'crash', 'surprise', 'other')),
  add column if not exists reason text check (char_length(reason) between 1 and 240);
create index if not exists cf_clips_source_idx on public.cf_clips (source_id);
create index if not exists cf_clips_created_idx on public.cf_clips (created_at desc, id desc);

create table if not exists public.cf_edits (
  id uuid primary key,
  segments jsonb not null check (jsonb_typeof(segments) = 'array' and jsonb_array_length(segments) between 1 and 10),
  options jsonb not null check (jsonb_typeof(options) = 'object'),
  status text not null default 'queued' check (status in ('queued', 'processing', 'completed', 'failed')),
  progress integer not null default 0 check (progress between 0 and 100),
  error text,
  attempts integer not null default 0,
  lease_token uuid,
  lease_until timestamptz,
  artifact_id uuid,
  duration double precision check (duration > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.cf_edits
  add column if not exists campaign_id uuid references public.cf_campaigns(id) on delete set null,
  add column if not exists ad jsonb check (ad is null or jsonb_typeof(ad) = 'object'),
  add column if not exists batch_id uuid,
  add column if not exists batch_position integer check (batch_position between 1 and 10);
create unique index if not exists cf_edits_batch_idx on public.cf_edits (batch_id, batch_position)
  where batch_id is not null;
create index if not exists cf_edits_campaign_idx on public.cf_edits (campaign_id) where campaign_id is not null;
create index if not exists cf_edits_queue_idx on public.cf_edits (created_at)
  where status in ('queued', 'processing');
create index if not exists cf_edits_created_idx on public.cf_edits (created_at desc, id desc);

create table if not exists public.cf_imports (
  id uuid primary key,
  host text not null,
  name text not null default '',
  options jsonb not null,
  source_id uuid references public.cf_sources(id) on delete set null,
  status text not null default 'queued' check (status in ('queued', 'processing', 'completed', 'failed')),
  progress integer not null default 0 check (progress between 0 and 100),
  error text,
  attempts integer not null default 0,
  lease_token uuid,
  lease_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists cf_imports_queue_idx on public.cf_imports (created_at)
  where status in ('queued', 'processing');
create index if not exists cf_imports_created_idx on public.cf_imports (created_at desc, id desc);

alter table public.cf_settings enable row level security;
alter table public.cf_campaigns enable row level security;
alter table public.cf_sources enable row level security;
alter table public.cf_clips enable row level security;
alter table public.cf_edits enable row level security;
revoke all on public.cf_settings, public.cf_campaigns, public.cf_sources, public.cf_clips from public, anon, authenticated;
grant select, insert, update on public.cf_settings, public.cf_campaigns, public.cf_sources to service_role;
grant select, insert, delete on public.cf_clips to service_role;
revoke all on public.cf_edits from public, anon, authenticated;
grant select, insert, update on public.cf_edits to service_role;
alter table public.cf_imports enable row level security;
revoke all on public.cf_imports from public, anon, authenticated;
grant select, insert, update on public.cf_imports to service_role;

create or replace function public.cf_claim(p_token uuid)
returns setof public.cf_sources language plpgsql security invoker set search_path = ''
as $$
begin
  update public.cf_sources
  set status = 'failed', error = 'Обработка прерывалась несколько раз. Запустите задачу повторно.',
      lease_token = null, lease_until = null, updated_at = now()
  where status = 'processing' and lease_until <= now() and attempts >= 3;

  return query
  with candidate as (
    select id from public.cf_sources
    where status = 'queued' or (status = 'processing' and lease_until <= now())
    order by created_at
    limit 1 for update skip locked
  )
  update public.cf_sources as s
  set status = 'processing', progress = 1, error = null, attempts = attempts + 1,
      lease_token = p_token, lease_until = now() + interval '120 seconds', updated_at = now()
  from candidate where s.id = candidate.id
  returning s.*;
end;
$$;

create or replace function public.cf_renew(p_id uuid, p_token uuid, p_progress integer)
returns boolean language plpgsql security invoker set search_path = ''
as $$
begin
  update public.cf_sources
  set lease_until = now() + interval '120 seconds',
      progress = greatest(progress, least(p_progress, 99)), updated_at = now()
  where id = p_id and status = 'processing' and lease_token = p_token and lease_until > now();
  return found;
end;
$$;

create or replace function public.cf_finish(p_id uuid, p_token uuid, p_clips jsonb, p_error text)
returns boolean language plpgsql security invoker set search_path = ''
as $$
begin
  update public.cf_sources
  set status = case when p_error is null then 'completed' else 'failed' end,
      progress = case when p_error is null then 100 else progress end, error = p_error,
      aspect = case when p_error is null then 'portrait' else aspect end,
      lease_token = null, lease_until = null, updated_at = now()
  where id = p_id and status = 'processing' and lease_token = p_token and lease_until > now();
  if not found then return false; end if;

  if p_error is null then
    delete from public.cf_clips where source_id = p_id;
    insert into public.cf_clips (id, source_id, title, start_seconds, end_seconds, score, category, reason)
    select c.id, p_id, c.title, c.start_seconds, c.end_seconds, c.score, c.category, c.reason
    from jsonb_to_recordset(p_clips) as c(
      id uuid, title text, start_seconds double precision, end_seconds double precision, score double precision,
      category text, reason text
    );
  end if;
  return true;
end;
$$;

create or replace function public.cf_summary()
returns jsonb language sql stable security invoker set search_path = ''
as $$
  select jsonb_build_object(
    'sources', count(*),
    'processing', count(*) filter (where status in ('queued', 'processing')),
    'failed', count(*) filter (where status = 'failed'),
    'clips', (select count(*) from public.cf_clips),
    'campaigns', (select count(*) from public.cf_campaigns where status = 'active')
  ) from public.cf_sources;
$$;

create or replace function public.cf_edit_claim(p_token uuid)
returns setof public.cf_edits language plpgsql security invoker set search_path = ''
as $$
begin
  update public.cf_edits
  set status = 'failed', error = 'Монтаж прерывался несколько раз. Запустите экспорт повторно.',
      lease_token = null, lease_until = null, updated_at = now()
  where status = 'processing' and lease_until <= now() and attempts >= 3;
  return query
  with candidate as (
    select id from public.cf_edits
    where status = 'queued' or (status = 'processing' and lease_until <= now())
    order by created_at
    limit 1 for update skip locked
  )
  update public.cf_edits as e
  set status = 'processing', progress = 1, error = null, attempts = attempts + 1,
      lease_token = p_token, lease_until = now() + interval '120 seconds', updated_at = now()
  from candidate where e.id = candidate.id
  returning e.*;
end;
$$;

create or replace function public.cf_edit_renew(p_id uuid, p_token uuid, p_progress integer)
returns boolean language plpgsql security invoker set search_path = ''
as $$
begin
  update public.cf_edits
  set lease_until = now() + interval '120 seconds',
      progress = greatest(progress, least(p_progress, 99)), updated_at = now()
  where id = p_id and status = 'processing' and lease_token = p_token and lease_until > now();
  return found;
end;
$$;

create or replace function public.cf_edit_finish(p_id uuid, p_token uuid, p_duration double precision, p_error text)
returns boolean language plpgsql security invoker set search_path = ''
as $$
begin
  update public.cf_edits
  set status = case when p_error is null then 'completed' else 'failed' end,
      progress = case when p_error is null then 100 else progress end,
      artifact_id = case when p_error is null then p_token else null end,
      duration = p_duration, error = p_error, lease_token = null, lease_until = null, updated_at = now()
  where id = p_id and status = 'processing' and lease_token = p_token and lease_until > now();
  return found;
end;
$$;

revoke all on function public.cf_claim(uuid), public.cf_renew(uuid, uuid, integer),
  public.cf_finish(uuid, uuid, jsonb, text), public.cf_summary() from public, anon, authenticated;
grant execute on function public.cf_claim(uuid), public.cf_renew(uuid, uuid, integer),
  public.cf_finish(uuid, uuid, jsonb, text), public.cf_summary() to service_role;
revoke all on function public.cf_edit_claim(uuid), public.cf_edit_renew(uuid, uuid, integer),
  public.cf_edit_finish(uuid, uuid, double precision, text) from public, anon, authenticated;
grant execute on function public.cf_edit_claim(uuid), public.cf_edit_renew(uuid, uuid, integer),
  public.cf_edit_finish(uuid, uuid, double precision, text) to service_role;

create or replace function public.cf_import_claim(p_token uuid)
returns setof public.cf_imports language plpgsql security invoker set search_path = ''
as $$
begin
  update public.cf_imports
  set status = 'failed', error = 'Импорт прерывался несколько раз. Запустите его повторно.',
      lease_token = null, lease_until = null, updated_at = now()
  where status = 'processing' and lease_until <= now() and attempts >= 3;
  return query
  with candidate as (
    select id from public.cf_imports
    where status = 'queued' or (status = 'processing' and lease_until <= now())
    order by created_at
    limit 1 for update skip locked
  )
  update public.cf_imports as i
  set status = 'processing', progress = 1, error = null, attempts = attempts + 1,
      lease_token = p_token, lease_until = now() + interval '120 seconds', updated_at = now()
  from candidate where i.id = candidate.id
  returning i.*;
end;
$$;

create or replace function public.cf_import_renew(p_id uuid, p_token uuid, p_progress integer)
returns boolean language plpgsql security invoker set search_path = ''
as $$
begin
  update public.cf_imports
  set lease_until = now() + interval '120 seconds',
      progress = greatest(progress, least(p_progress, 99)), updated_at = now()
  where id = p_id and status = 'processing' and lease_token = p_token and lease_until > now();
  return found;
end;
$$;

create or replace function public.cf_import_finish(p_id uuid, p_token uuid, p_source jsonb, p_error text)
returns boolean language plpgsql security invoker set search_path = ''
as $$
declare new_source uuid;
begin
  update public.cf_imports
  set status = case when p_error is null then 'completed' else 'failed' end,
      progress = case when p_error is null then 100 else progress end,
      error = p_error, lease_token = null, lease_until = null, updated_at = now()
  where id = p_id and status = 'processing' and lease_token = p_token and lease_until > now();
  if not found then return false; end if;
  if p_error is null then
    insert into public.cf_sources (
      id, name, extension, byte_size, duration, has_audio, clip_seconds, clip_count, aspect, campaign_id
    )
    select s.id, s.name, s.extension, s.byte_size, s.duration, s.has_audio,
      s.clip_seconds, s.clip_count, s.aspect, c.id
    from jsonb_to_record(p_source) as s(
      id uuid, name text, extension text, byte_size bigint, duration double precision,
      has_audio boolean, clip_seconds integer, clip_count integer, aspect text, campaign_id uuid
    )
    left join public.cf_campaigns c on c.id = s.campaign_id
    returning id into new_source;
    update public.cf_imports set source_id = new_source where id = p_id;
  end if;
  return true;
end;
$$;

revoke all on function public.cf_import_claim(uuid), public.cf_import_renew(uuid, uuid, integer),
  public.cf_import_finish(uuid, uuid, jsonb, text) from public, anon, authenticated;
grant execute on function public.cf_import_claim(uuid), public.cf_import_renew(uuid, uuid, integer),
  public.cf_import_finish(uuid, uuid, jsonb, text) to service_role;

notify pgrst, 'reload schema';
commit;
